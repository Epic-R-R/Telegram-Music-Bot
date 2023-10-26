import datetime
import logging
import queue as queuem
import re
import sys
import threading
import traceback
from typing import List, Optional, Union
from os.path import basename

import requests
import sqlalchemy
import telegram
import youtube_dl

import database as db
import localization
import nuconfig
import utils

# Setup logging
log = logging.getLogger(__name__)


class StopSignal:
    """A data class for signaling a worker to stop."""

    def __init__(self, reason: str = ""):
        self.reason = reason


class CancelSignal:
    """A class for signaling a cancellation."""


class Worker(threading.Thread):
    """A worker thread for handling a single conversation."""

    def __init__(
        self,
        bot,
        chat: telegram.Chat,
        telegram_user: telegram.User,
        cfg: nuconfig.NuConfig,
        engine,
        *args,
        **kwargs,
    ):
        super().__init__(name=f"Worker {chat.id}", *args, **kwargs)
        self.bot, self.chat, self.telegram_user, self.cfg = (
            bot,
            chat,
            telegram_user,
            cfg,
        )
        self.cancel_keyboard = [[telegram.KeyboardButton("🔙 Cancel")]]
        self.ydl_opts = {"extract_flat": True, "dumpjson": True}
        self.ITEMS_PER_PAGE = 15
        self.MAX_RETRIES = 3
        self.session = sqlalchemy.orm.sessionmaker(bind=engine)()
        self.user: Optional[db.User] = None
        self.admin: Optional[db.Admin] = None
        self.queue = queuem.Queue()

    def run(self):
        """The main conversation handling code."""
        log.debug("Starting conversation")
        # Get the user db data from the users and admin tables
        self.user = (
            self.session.query(db.User)
            .filter(db.User.user_id == self.chat.id)
            .one_or_none()
        )
        self.admin = (
            self.session.query(db.Admin)
            .filter(db.Admin.user_id == self.chat.id)
            .one_or_none()
        )
        # If the user isn't registered, create a new record and add it to the db
        new_user = False
        if self.user is None:
            new_user = True
            # Check if there are other registered users: if there aren't any, the first user will be owner of the bot
            will_be_owner = self.session.query(db.Admin).first() is None
            # Create the new record
            self.user = db.User(w=self)
            # Add the new record to the db
            self.session.add(self.user)
            # If the will be owner flag is set
            if will_be_owner:
                # Become owner
                self.admin = db.Admin(user=self.user, is_owner=True)
                # Add the admin to the transaction
                self.session.add(self.admin)
            # Commit the transaction
            self.session.commit()
            log.info(f"Created new user: {self.user}")
            if will_be_owner:
                log.warning(
                    f"User was auto-promoted to Admin as no other admins existed: {self.user}"
                )
        # Create the localization object
        self._create_localization()

        # Capture exceptions that occour during the conversation
        if new_user:
            admins = self.session.query(db.Admin).all()
            num = self.session.query(db.User).all()
            for admin in admins:
                self.bot.send_message(
                    chat_id=admin.user_id,
                    text=self.loc.get(
                        "new_user_in", number=len(num), new=self.user.identifiable_str()
                    ),
                )
        # noinspection PyBroadException
        try:
            # If the user is not an admin, send him to the user menu
            if self.admin is None:
                self.__user_menu()

            # If the user is an admin, send him to the admin menu
            else:
                # Open the admin menu
                self._admin_menu()
        except Exception as e:
            # Try to notify the user of the exception
            # noinspection PyBroadException
            try:
                self.bot.send_message(
                    self.chat.id, self.loc.get("fatal_conversation_exception")
                )
            except Exception as ne:
                log.error(
                    f"Failed to notify the user of a conversation exception: {ne}"
                )
            log.error(f"Exception in {self}: {e}")
            traceback.print_exception(*sys.exc_info())

    def is_ready(self):
        """Check if the worker is ready."""
        return self.loc is not None

    def stop(self, reason: str = ""):
        """Gracefully stop the worker process"""
        # Send a stop message to the thread
        self.queue.put(StopSignal(reason))
        # Wait for the thread to stop
        self.join()

    # noinspection PyUnboundLocalVariable
    def __receive_next_update(self) -> telegram.Update:
        """Get the next update from the queue."""
        # Pop data from the queue
        try:
            data = self.queue.get(timeout=self.cfg["Telegram"]["conversation_timeout"])
        except queuem.Empty:
            # If the conversation times out, gracefully stop the thread
            self._graceful_stop(StopSignal("timeout"))
        # Check if the data is a stop signal instance
        if isinstance(data, StopSignal):
            # Gracefully stop the process
            self._graceful_stop(data)
        # Return the received update
        return data

    def _wait_for_specific_message(
        self, items: List[str], cancellable: bool = False
    ) -> Union[str, CancelSignal]:
        """Continue getting updates until until one of the strings contained in the list is received as a message."""
        log.debug("Waiting for a specific message...")
        while True:
            # Get the next update
            update = self.__receive_next_update()
            # If a CancelSignal is received...
            if isinstance(update, CancelSignal):
                # And the wait is cancellable...
                if cancellable:
                    # Return the CancelSignal
                    return update, "1"
                else:
                    # Ignore the signal
                    continue
            # Ensure the update contains a message
            if update.message is None:
                continue
            # Ensure the message contains text
            if update.message.text is None:
                continue
            # Check if the message is contained in the list
            if update.message.text not in items:
                continue
            # Return the message text
            return update.message.text, update.message.message_id

    def __wait_for_regex(
        self, regex: str, cancellable: bool = False
    ) -> Union[str, CancelSignal]:
        """Continue getting updates until the regex finds a match in a message, then return the first capture group."""
        log.debug("Waiting for a regex...")
        while True:
            # Get the next update
            update = self.__receive_next_update()
            # If a CancelSignal is received...
            if isinstance(update, CancelSignal):
                # And the wait is cancellable...
                if cancellable:
                    # Return the CancelSignal
                    return update, "1"
                else:
                    # Ignore the signal
                    continue
            # Ensure the update contains a message
            if update.message is None:
                continue
            # Ensure the message contains text
            if update.message.text is None:
                continue
            # Try to match the regex with the received message
            match = re.search(regex, update.message.text, re.DOTALL)
            # Ensure there is a match
            if match is None:
                continue
            return match.group(1), update.message.message_id

    def __wait_for_inlinekeyboard_callback(
        self, cancellable: bool = False
    ) -> Union[telegram.CallbackQuery, CancelSignal]:
        """Continue getting updates until an inline keyboard callback is received, then return it."""
        log.debug("Waiting for a CallbackQuery...")
        while True:
            # Get the next update
            update = self.__receive_next_update()
            # If a CancelSignal is received...
            if isinstance(update, CancelSignal):
                # And the wait is cancellable...
                if cancellable:
                    # Return the CancelSignal
                    return update
                else:
                    # Ignore the signal
                    continue
            # Ensure the update is a CallbackQuery
            if update.callback_query is None:
                continue
            # Answer the callbackquery
            self.bot.answer_callback_query(update.callback_query.id)
            # Return the callbackquery
            return update.callback_query

    def __user_menu(self):
        """Display the user menu."""
        log.debug("Displaying __user_menu")
        keyboard = self.__create_keyboard()
        message_user_menu = self.__send_user_menu_message(keyboard)

        while True:
            select = self.__wait_for_inlinekeyboard_callback()
            self.__handle_menu_selection(select, message_user_menu)

    def __create_keyboard(self):
        """Create and return the user menu keyboard."""
        return telegram.InlineKeyboardMarkup(
            [
                [
                    telegram.InlineKeyboardButton(
                        text=self.loc.get("menu_soundcloud"),
                        callback_data="cmd_soundcloud",
                    ),
                    telegram.InlineKeyboardButton(
                        text=self.loc.get("menu_spotify"),
                        callback_data="cmd_spotify",
                    ),
                ],
                [
                    telegram.InlineKeyboardButton(
                        text=self.loc.get("menu_youtube"),
                        callback_data="cmd_youtube",
                    ),
                    telegram.InlineKeyboardButton(
                        text=self.loc.get("menu_deezer"), callback_data="cmd_deezer"
                    ),
                ],
            ]
        )

    def __send_user_menu_message(self, keyboard):
        """Send the user menu message and return the message object."""
        return self.bot.send_message(
            chat_id=self.chat.id,
            text=self.loc.get("conversation_open_user_menu"),
            reply_markup=keyboard,
        )

    def __handle_menu_selection(self, select, message_user_menu):
        """Handle the user's menu selection."""
        command_map = {
            "cmd_soundcloud": self._soundcloud,
            "cmd_spotify": self._spotify,
            "cmd_youtube": self._youtube,
            "cmd_deezer": self._deezer,
        }

        command = command_map.get(select.data)
        if command:
            self.bot.delete_message(
                chat_id=self.chat.id, message_id=message_user_menu["message_id"]
            )
            command()

    def _get_link(self, msg_link):
        i = 0
        while i < self.MAX_RETRIES:
            link, msg_id = self.__wait_for_regex(r"(.*)", cancellable=True)
            if isinstance(link, CancelSignal):
                self._clean_up_messages(i, msg_link)
                self.__user_menu()
            try:
                url = re.search("(?P<url>https?://[^\s]+)", link).group("url")
                if "soundcloud" in url:
                    return url, msg_id
            except AttributeError:
                pass  # Invalid URL, retry
            self._handle_invalid_link(i, msg_link)
            i += 1
        self.bot.send_message(chat_id=self.chat.id, text=self.loc.get("invalid_link"))

    def _handle_invalid_link(self, i, msg_link):
        if i >= 1:
            self.bot.delete_message(
                chat_id=self.chat.id, message_id=msg_link["message_id"]
            )
        self.bot.send_message(
            chat_id=self.chat.id,
            text=self.loc.get("invalid_link"),
            reply_markup=telegram.ReplyKeyboardMarkup(
                self.cancel_keyboard, resize_keyboard=True, one_time_keyboard=True
            ),
        )

    def _clean_up_messages(self, i, msg_link):
        if i == 1:
            self.bot.delete_message(
                chat_id=self.chat.id, message_id=msg_link["message_id"]
            )

    def _soundcloud(self):
        msg_link = self.bot.send_message(
            self.chat.id,
            self.loc.get("msg_link"),
            reply_markup=telegram.ReplyKeyboardMarkup(
                self.cancel_keyboard, resize_keyboard=True, one_time_keyboard=True
            ),
        )
        link, msg_id = self._get_link(msg_link)
        if link is None:
            return  # Exit if no valid link is provided
        try:
            self.bot.delete_message(
                chat_id=self.chat.id, message_id=msg_link["message_id"]
            )
            start_msg = self.bot.send_message(
                chat_id=self.chat.id,
                text=self.loc.get("menu_loading"),
                reply_to_message_id=msg_id,
            )
            self._handle_extraction(link, start_msg)
        except Exception as e:
            self.bot.send_message(
                chat_id=self.chat.id, text=self.loc.get("invalid_link")
            )
            print(f"Error: {e}")

    def _handle_extraction(self, link, start_msg):
        with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
            data = ydl.extract_info(url=link, download=False)
        if "_type" in data:
            if data["_type"] == "playlist":
                self._handle_playlist(data, start_msg)
            elif data["_type"] == "url":
                self._handle_url(data, start_msg)
        else:
            self.__show_one(data=data, message_id=start_msg["message_id"])

    def _handle_playlist(self, data, start_msg):
        self.bot.edit_message_text(
            chat_id=self.chat.id,
            message_id=start_msg["message_id"],
            text=self.loc.get(
                "album_caption",
                title=data["title"],
                tracks=len(data["entries"]),
                url=data["webpage_url"],
            ),
        )
        tracks = []
        collect = self.bot.send_message(
            chat_id=self.chat.id,
            text=self.loc.get(
                "get_information",
                track="0",
                all_tracks=len(data["entries"]),
            ),
        )
        for da in data["entries"]:
            self.bot.edit_message_text(
                chat_id=self.chat.id,
                text=self.loc.get(
                    "get_information",
                    track=data["entries"].index(da) + 1,
                    all_tracks=len(data["entries"]),
                ),
                message_id=collect["message_id"],
            )
            with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
                tracks.append(ydl.extract_info(url=da["url"], download=False))
        self.bot.delete_message(chat_id=self.chat.id, message_id=collect["message_id"])
        self.__show_many(
            data_list=tracks,
            page=0,
            ydl_opts=self.ydl_opts,
            message_id=start_msg["message_id"],
        )

    def _handle_url(self, data, start_msg):
        with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
            data = ydl.extract_info(url=data["url"], download=False)
        if data.get("_type"):
            self._handle_playlist(data, start_msg)
        self.__show_one(data=data, message_id=start_msg["message_id"])

    def _create_format_buttons(self, formats):
        """Create buttons for available formats."""
        format_buttons = []
        format_urls = []  # Store the URLs separately
        format_seen = set()  # Track seen formats
        for format_info in formats:
            if format_info["abr"] > 64 and ".m3u8" not in format_info["url"]:
                format_key = f"{format_info['abr']} kbps {format_info['ext'].upper()}"
                if format_key not in format_seen:
                    format_buttons.append(
                        telegram.InlineKeyboardButton(
                            text=f"Download {format_key}",
                            callback_data=f"download_format:{len(format_urls)}",  # Use index as callback data
                        )
                    )
                    format_urls.append(format_info["url"])  # Store the URL
                    format_seen.add(format_key)  # Mark format as seen
        return format_buttons, format_urls

    def _get_best_thumbnail(self, thumbnails):
        """Return the best thumbnail based on resolution."""
        best_thumbnail = None
        for thumbnail_info in thumbnails:
            if "resolution" in thumbnail_info:
                if (
                    not best_thumbnail
                    or thumbnail_info["width"] > best_thumbnail["width"]
                    and utils.check_thumbnail(best_thumbnail["url"])
                ):
                    best_thumbnail = thumbnail_info
        return best_thumbnail

    def _create_buttons(self, page_data):
        """Create buttons for each data item."""
        buttons = [
            telegram.InlineKeyboardButton(
                text=f"{i + 1}. {data['title']}", callback_data=f"button_{i}"
            )
            for i, data in enumerate(page_data)
        ]
        return buttons

    def _create_navigation_buttons(self, page, data_list_length):
        """Create navigation buttons for pagination."""
        navigation_buttons = []
        if page > 0:
            navigation_buttons.append(
                telegram.InlineKeyboardButton(
                    text=self.loc.get("menu_previous"), callback_data="prev"
                )
            )
        if (page + 1) * self.ITEMS_PER_PAGE < data_list_length:
            navigation_buttons.append(
                telegram.InlineKeyboardButton(
                    text=self.loc.get("menu_next"), callback_data="next"
                )
            )
        navigation_buttons.append(
            telegram.InlineKeyboardButton(
                text=self.loc.get("menu_cancel"), callback_data="cmd_cancel"
            )
        )
        return navigation_buttons

    def __show_one(self, data, message_id, data_list=None):
        best_thumbnail = self._get_best_thumbnail(data["thumbnails"])
        thumbnail_button = (
            telegram.InlineKeyboardButton(
                text="Download Cover", callback_data="download_cover"
            )
            if best_thumbnail
            else None
        )

        format_buttons, format_urls = self._create_format_buttons(data["formats"])

        # Create inline keyboard markup with the thumbnail button and format buttons
        keyboard = []
        if thumbnail_button:
            keyboard.append([thumbnail_button])
        if format_buttons:
            keyboard.append(format_buttons)
        keyboard.append(
            [
                telegram.InlineKeyboardButton(
                    text=self.loc.get("menu_cancel"), callback_data="cmd_cancel"
                )
            ]
        )

        reply_markup = telegram.InlineKeyboardMarkup(keyboard)

        # Send the message with inline buttons
        self.bot.edit_message_text(
            chat_id=self.chat.id,
            message_id=message_id,
            text=f"Title: {data['title']}\nURL: {data['webpage_url']}",
            reply_markup=reply_markup,
        )

        select = self.__wait_for_inlinekeyboard_callback(cancellable=True)
        if isinstance(select, CancelSignal):
            if data_list is None:
                self.bot.delete_message(chat_id=self.chat.id, message_id=message_id)
                self.__user_menu()
            else:
                self.__show_many(data_list, 0, self.ydl_opts, message_id)
        elif select.data == "download_cover":
            self.bot.delete_message(chat_id=self.chat.id, message_id=message_id)
            self.bot.send_photo(
                chat_id=self.chat.id,
                photo=requests.get(url=best_thumbnail["url"], stream=True).content,
                filename=basename(select.data),
                parse_mode="HTML",
            )
            self.__user_menu()

        elif select.data.startswith("download_format:"):
            self.__send_file(select, data, format_urls, message_id, best_thumbnail)

    def __show_many(self, data_list, page, ydl_opts, message_id):
        start_idx = page * self.ITEMS_PER_PAGE
        end_idx = (page + 1) * self.ITEMS_PER_PAGE
        page_data = data_list[start_idx:end_idx]

        buttons = self._create_buttons(page_data)
        keyboard = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

        navigation_buttons = self._create_navigation_buttons(page, len(data_list))
        keyboard.append(navigation_buttons)

        reply_markup = telegram.InlineKeyboardMarkup(keyboard)
        self.bot.edit_message_reply_markup(
            chat_id=self.chat.id,
            message_id=message_id,
            reply_markup=reply_markup,
        )

        select = self.__wait_for_inlinekeyboard_callback()
        if select.data == "cmd_cancel":
            self.bot.delete_message(chat_id=self.chat.id, message_id=message_id)
            self.__user_menu()
        elif select.data in ["next", "prev"]:
            new_page = page + 1 if select.data == "next" else page - 1
            self.__show_many(data_list, new_page, ydl_opts, message_id)
        else:
            selected_item_idx = int(select.data.split("_")[1])
            selected_data_item = page_data[selected_item_idx]
            self.__show_one(selected_data_item, message_id, data_list)

    def _send_audio_file(self, url, headers, title, uploader, thumb_url):
        """Send the audio file to the user."""
        try:
            audio_content = requests.get(url, stream=True, headers=headers).content
            thumb_content = requests.get(url=thumb_url, stream=True).content
            self.bot.send_audio(
                chat_id=self.chat.id,
                audio=audio_content,
                caption=self.loc.get("caption", title=title),
                parse_mode="HTML",
                title=f"{uploader} - {title}",
                filename=f"{uploader} - {title}.mp3",
                thumb=thumb_content,
            )
        except Exception as e:
            log.error(f"Failed to send audio: {e}")
            # Optionally re-raise the exception if you want to handle it at a higher level
            # raise

    def __send_file(self, select, data, format_urls, message_id, best_thumbnail):
        format_index = int(select.data[len("download_format:") :])
        if format_index < len(format_urls):
            self.bot.delete_message(chat_id=self.chat.id, message_id=message_id)
            best_thumbnail = self._get_best_thumbnail(data["thumbnails"])
            if best_thumbnail:
                self._send_audio_file(
                    url=format_urls[format_index],
                    headers=data["http_headers"],
                    title=data["title"],
                    uploader=data["uploader"],
                    thumb_url=best_thumbnail["url"],
                )
            self.__user_menu()

    def _notify_updating(self, service_name: str):
        """Notify the user that a service is under updating."""
        update_message = self.loc.get("under_updating").format(service_name)
        self.bot.send_message(chat_id=self.chat.id, text=update_message)
        self.__user_menu()

    def _spotify(self):
        """Handle Spotify-related requests."""
        self._notify_updating("Spotify")

    def _youtube(self):
        """Handle YouTube-related requests."""
        self._notify_updating("YouTube")

    def _deezer(self):
        """Handle Deezer-related requests."""
        self._notify_updating("Deezer")

    def _admin_menu(self):
        """Display the admin menu."""
        log.debug("Displaying _admin_menu")
        while True:
            self._send_admin_menu()
            selection = self._wait_for_user_selection()
            if selection[0] == self.loc.get("menu_user_mode"):
                self._switch_to_user_menu()

    def _send_admin_menu(self):
        """Send the admin menu to the user."""
        keyboard = [[self.loc.get("menu_user_mode")]]
        self.bot.send_message(
            self.chat.id,
            self.loc.get("conversation_open_admin_menu"),
            reply_markup=telegram.ReplyKeyboardMarkup(keyboard, one_time_keyboard=True),
        )

    def _wait_for_user_selection(self):
        """Wait for a reply from the user and return the selection."""
        return self._wait_for_specific_message([self.loc.get("menu_user_mode")])

    def _switch_to_user_menu(self):
        """Switch to the user menu."""
        self.bot.send_message(
            self.chat.id, self.loc.get("conversation_switch_to_user_mode")
        )
        self.__user_menu()

    def _create_localization(self):
        """Create a localization object."""
        self.loc = localization.Localization(
            language=self.user.language,
            fallback=self.cfg["Language"]["fallback_language"],
            replacements={
                "user_string": str(self.user),
                "user_mention": self.user.mention(),
                "user_full_name": self.user.full_name,
                "user_first_name": self.user.first_name,
                "today": datetime.datetime.now().strftime("%a %d %b %Y"),
            },
        )

    def _graceful_stop(self, stop_trigger: StopSignal):
        """Handle the graceful stop of the thread."""
        log.debug("Gracefully stopping the conversation")

        if stop_trigger.reason == "timeout":
            self._notify_session_expiration()

        self._close_resources()

    def _notify_session_expiration(self):
        """Notify the user that the session has expired and remove the keyboard."""
        self.bot.send_message(
            self.chat.id,
            self.loc.get("conversation_expired"),
            reply_markup=telegram.ReplyKeyboardRemove(),
        )

    def _close_resources(self):
        """Close any open resources, such as the database session, and exit."""
        self.session.close()
        sys.exit(0)
