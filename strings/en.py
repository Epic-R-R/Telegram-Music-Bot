# Conversation: to send an inline keyboard you need to send a message with it
conversation_open_user_menu = (
    "What would you like to do?\n" "Choose an option from menu below"
)

# Conversation: like above, but for administrators
conversation_open_admin_menu = (
    "You are a 💼 <b>Manager</b> of this store!\n"
    "What would you like to do?\n"
    "\n"
    "<i>Press a key on the bottom keyboard to select an operation.\n"
    "If the keyboard has not opened, you can open it by pressing the button with four small"
    " squares in the message bar.</i>"
)

# Conversation: select a channel to edit
conversation_admin_select_channel = "✏️ What product do you want to edit?"

# Conversation: select a product to delete
conversation_admin_select_product_to_delete = "❌ What product do you want to delete?"

# Conversation: select a user to edit
conversation_admin_select_user = "Select an user to edit."

#
# Conversation: confirm promotion to admin
conversation_confirm_admin_promotion = (
    "Are you sure you want to promote this user to 💼 Manager?\n"
    "It is an irreversible action!"
)

# Conversation: switching to user mode
conversation_switch_to_user_mode = (
    " You are switching to 👤 Customer mode.\n"
    "If you want to go back to the 💼 Manager menu, restart the conversation with /start."
)

# Notification: the conversation has expired
conversation_expired = (
    "🕐  I haven't received any messages in a while, so I closed the conversation to save"
    " resources.\n"
    "If you want to start a new one, send a new /start command."
)
# Admin menu: go to user mode
menu_user_mode = "👤 Switch to customer mode"

# Menu: cancel
menu_cancel = "🔙 Cancel"

# Menu: next page
menu_next = "▶️ Next"

# Menu: previous page
menu_previous = "◀️ Previous"

# Emoji: yes
emoji_yes = "✅"

# Emoji: no
emoji_no = "🚫"

# Suggest the creation of a new worker with /start
error_no_worker_for_chat = "⚠️ Bot was updated.\n" "send the /start command to the bot."

# Error: a message was sent in a chat, but the worker for that chat is not ready.
error_worker_not_ready = (
    "🕒 The conversation with the bot is currently starting.\n"
    "Please, wait a few moments before sending more commands!"
)

# Fatal: conversation raised an exception
fatal_conversation_exception = (
    "☢️ Oh no! An <b>error</b> interrupted this conversation\n"
    "The error was reported to the bot owner so that he can fix it.\n"
    "To restart the conversation, send the /start command again."
)

# Soundcloud menu
menu_soundcloud = "Soundcloud 🎧"

album_caption = (
    "┌💽 Info\n"
    "├🎵 Title: {title}\n"
    "├📊 Tracks: {tracks}\n"
    "└🔗 URL: {url}\n"
    "<i>For more details you can use buttons</i>"
)

get_information = "Get information's for track <b>{track}</b> of <b>{all_tracks}</b>"

caption = "<a href='t.me/Epic_R_R'>{title}</a>"

# Message download
msg_link = (
    "To download, "
    "just send the link of the desired song, "
    "playlist or album to the robot."
)

# Spotify menu
menu_spotify = "Spotify 🎧"

# Youtube menu
menu_youtube = "Youtube 🗺"
# Deezer
menu_deezer = "Deezer"

# Menu: Loading
menu_loading = "<i>⏳ Loading This may take a few moments... ⏳</i>"

# Updating
under_updating = "This feature is under updating."

# New user join
new_user_in = "New User {new}\nTotal member: {number}"

# Invalid message
invalid_link = "Invalid link, try again."
