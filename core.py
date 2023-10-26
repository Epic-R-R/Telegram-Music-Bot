from os import path
from sys import exit
from threading import current_thread
from logging import basicConfig, getLogger, StreamHandler, Formatter, INFO, ERROR
from sqlalchemy import create_engine
import sqlalchemy.ext.declarative as sed
from telegram.utils.request import Request
from telegram import ReplyKeyboardRemove, CallbackQuery, PreCheckoutQuery, error

from database import TableDeclarativeBase
from duckbot import factory
from localization import Localization
from nuconfig import NuConfig
from worker import Worker, CancelSignal

try:
    from coloredlogs import ColoredFormatter as Formatter
except ImportError:
    pass  # Use the default Formatter imported from logging


def load_config():
    config_file_path = "config/config.toml"
    template_config_path = "config/template_config.toml"

    if not path.isfile(template_config_path):
        raise FileNotFoundError(f"{template_config_path} does not exist!")

    if not path.isfile(config_file_path):
        with open(template_config_path, encoding="utf8") as template, open(
            config_file_path, "w", encoding="utf8"
        ) as config:
            config.write(template.read())
        raise FileNotFoundError(
            f"{config_file_path} has been created from template. Customize it, then restart the bot."
        )

    with open(template_config_path, encoding="utf8") as template, open(
        config_file_path, encoding="utf8"
    ) as config:
        template_cfg, user_cfg = NuConfig(template), NuConfig(config)
        if not template_cfg.cmplog(user_cfg):
            raise ValueError(
                "Errors found in config file. Please fix them and restart the bot."
            )

    return user_cfg


def setup_logging(log_level, log_format):
    basicConfig(
        filename="log.log",
        filemode="a",
        format=log_format,
        level=log_level,
        style="{",
    )
    root_log = getLogger()
    root_log.setLevel(log_level)
    stream_handler = StreamHandler()
    stream_handler.setFormatter(Formatter(log_format, style="{"))
    root_log.handlers.clear()
    root_log.addHandler(stream_handler)


def setup_database(db_engine):
    engine = create_engine(db_engine)
    TableDeclarativeBase.metadata.bind = engine
    TableDeclarativeBase.metadata.create_all()
    sed.DeferredReflection.prepare(engine)
    return engine


def initialize_bot(user_cfg):
    bot = factory(user_cfg)(request=Request(user_cfg["Telegram"]["con_pool_size"]))
    me = bot.get_me()
    if me is None:
        raise ValueError("Invalid bot token provided in the config file.")
    return bot, me


def main():
    current_thread().name = "Core"

    try:
        user_cfg = load_config()
        setup_logging(user_cfg["Logging"]["level"], user_cfg["Logging"]["format"])
        engine = setup_database(user_cfg["Database"]["engine"])
        bot, me = initialize_bot(user_cfg)
    except (FileNotFoundError, ValueError) as e:
        getLogger("core").fatal(e)
        exit(1)

    # Finding default language
    default_language = user_cfg["Language"]["default_language"]
    # Creating localization object
    default_loc = Localization(language=default_language, fallback=default_language)

    # Create a dictionary linking the chat ids to the Worker objects
    chat_workers = {}

    # Current update offset; if None it will get the last 100 unparsed messages
    next_update = None

    # Notify on the console that the bot is starting
    getLogger("core").info(f"@{me.username} is starting!")

    # Main loop of the program
    while True:
        # Get a new batch of 100 updates and mark the last 100 parsed as read
        update_timeout = user_cfg["Telegram"]["long_polling_timeout"]
        getLogger("core").debug(
            f"Getting updates from Telegram with a timeout of {update_timeout} seconds"
        )
        updates = bot.get_updates(offset=next_update, timeout=update_timeout)
        # Parse all the updates
        for update in updates:
            # If the update is a message...
            if update.message is not None:
                # Ensure the message has been sent in a private chat
                if update.message.chat.type != "private":
                    getLogger("core").debug(
                        f"Received a message from a non-private chat: {update.message.chat.id}"
                    )
                    # Notify the chat
                    bot.send_message(
                        update.message.chat.id, default_loc.get("error_nonprivate_chat")
                    )
                    # Skip the update
                    continue
                # If the message is a start command...
                if isinstance(
                    update.message.text, str
                ) and update.message.text.startswith("/start"):
                    getLogger("core").info(
                        f"Received /start from: {update.message.chat.id}"
                    )
                    # Check if a worker already exists for that chat
                    old_worker = chat_workers.get(update.message.chat.id)
                    # If it exists, gracefully stop the worker
                    if old_worker:
                        getLogger("core").debug(
                            f"Received request to stop {old_worker.name}"
                        )
                        old_worker.stop("request")
                    # Initialize a new worker for the chat
                    new_worker = Worker(
                        bot=bot,
                        chat=update.message.chat,
                        telegram_user=update.message.from_user,
                        cfg=user_cfg,
                        engine=engine,
                        daemon=True,
                    )
                    # Start the worker
                    getLogger("core").debug(f"Starting {new_worker.name}")
                    new_worker.start()
                    # Store the worker in the dictionary
                    chat_workers[update.message.chat.id] = new_worker
                    # Skip the update
                    continue
                # Otherwise, forward the update to the corresponding worker
                receiving_worker = chat_workers.get(update.message.chat.id)
                # Ensure a worker exists for the chat and is alive
                if receiving_worker is None:
                    getLogger("core").debug(
                        f"Received a message in a chat without worker: {update.message.chat.id}"
                    )
                    # Suggest that the user restarts the chat with /start
                    bot.send_message(
                        update.message.chat.id,
                        default_loc.get("error_no_worker_for_chat"),
                        reply_markup=ReplyKeyboardRemove(),
                    )
                    # Skip the update
                    continue
                # If the worker is not ready...
                if not receiving_worker.is_ready():
                    getLogger("core").debug(
                        f"Received a message in a chat where the worker wasn't ready yet: {update.message.chat.id}"
                    )
                    # Suggest that the user restarts the chat with /start
                    bot.send_message(
                        update.message.chat.id,
                        default_loc.get("error_worker_not_ready"),
                        reply_markup=ReplyKeyboardRemove(),
                    )
                    # Skip the update
                    continue
                # If the message contains the "Cancel" string defined in the strings file...
                if update.message.text == receiving_worker.loc.get("menu_cancel"):
                    getLogger("core").debug(
                        f"Forwarding CancelSignal to {receiving_worker}"
                    )
                    # Send a CancelSignal to the worker instead of the update
                    receiving_worker.queue.put(CancelSignal())
                else:
                    getLogger("core").debug(f"Forwarding message to {receiving_worker}")
                    # Forward the update to the worker
                    receiving_worker.queue.put(update)
            # If the update is a inline keyboard press...
            if isinstance(update.callback_query, CallbackQuery):
                # Forward the update to the corresponding worker
                receiving_worker = chat_workers.get(update.callback_query.from_user.id)
                # Ensure a worker exists for the chat
                if receiving_worker is None:
                    getLogger("core").debug(
                        f"Received a callback query in a chat without worker: {update.callback_query.from_user.id}"
                    )
                    # Suggest that the user restarts the chat with /start
                    bot.send_message(
                        update.callback_query.from_user.id,
                        default_loc.get("error_no_worker_for_chat"),
                    )
                    # Skip the update
                    continue
                # Check if the pressed inline key is a cancel button
                if update.callback_query.data == "cmd_cancel":
                    getLogger("core").debug(
                        f"Forwarding CancelSignal to {receiving_worker}"
                    )
                    # Forward a CancelSignal to the worker
                    receiving_worker.queue.put(CancelSignal())
                    # Notify the Telegram client that the inline keyboard press has been received
                    bot.answer_callback_query(update.callback_query.id)
                else:
                    getLogger("core").debug(
                        f"Forwarding callback query to {receiving_worker}"
                    )
                    # Forward the update to the worker
                    receiving_worker.queue.put(update)
            # If the update is a precheckoutquery, ensure it hasn't expired before forwarding it
            if isinstance(update.pre_checkout_query, PreCheckoutQuery):
                # Forward the update to the corresponding worker
                receiving_worker = chat_workers.get(
                    update.pre_checkout_query.from_user.id
                )
                # Check if it's the active invoice for this chat
                if (
                    receiving_worker is None
                    or update.pre_checkout_query.invoice_payload
                    != receiving_worker.invoice_payload
                ):
                    # Notify the user that the invoice has expired
                    getLogger("core").debug(
                        f"Received a pre-checkout query for an expired invoice in: {update.pre_checkout_query.from_user.id}"
                    )
                    try:
                        bot.answer_pre_checkout_query(
                            update.pre_checkout_query.id,
                            ok=False,
                            error_message=default_loc.get("error_invoice_expired"),
                        )
                    except error.BadRequest:
                        getLogger("core").error(
                            "pre-checkout query expired before an answer could be sent!"
                        )
                    # Go to the next update
                    continue
                getLogger("core").debug(
                    f"Forwarding pre-checkout query to {receiving_worker}"
                )
                # Forward the update to the worker
                receiving_worker.queue.put(update)
        # If there were any updates...
        if len(updates):
            # Mark them as read by increasing the update_offset
            next_update = updates[-1].update_id + 1


if __name__ == "__main__":
    main()
