"""
RH Radar - Subscriber Management
Tracks who has hit /start on the bot, so alerts broadcast to everyone
currently subscribed instead of one fixed chat. Also stores each
subscriber's OWN copy of a token's "NEW" alert message_id, so a later
milestone ping ("🚀 5x reached") can reply/thread correctly under that
specific person's message - not just one shared channel's message.
"""


def init_subscribers(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            joined_at TEXT,
            active INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriber_messages (
            token_address TEXT,
            chat_id INTEGER,
            message_id INTEGER,
            PRIMARY KEY (token_address, chat_id)
        )
    """)
    conn.commit()


def add_subscriber(conn, chat_id, username, now_iso):
    """Adds a new subscriber, or reactivates one who previously /stop'd."""
    conn.execute("""
        INSERT INTO subscribers (chat_id, username, joined_at, active)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(chat_id) DO UPDATE SET active = 1, username = excluded.username
    """, (chat_id, username, now_iso))
    conn.commit()


def remove_subscriber(conn, chat_id):
    conn.execute("UPDATE subscribers SET active = 0 WHERE chat_id = ?", (chat_id,))
    conn.commit()


def get_active_subscribers(conn):
    rows = conn.execute("SELECT chat_id FROM subscribers WHERE active = 1").fetchall()
    return [r[0] for r in rows]


def subscriber_count(conn):
    row = conn.execute("SELECT COUNT(*) FROM subscribers WHERE active = 1").fetchone()
    return row[0] if row else 0


def record_subscriber_message(conn, token_address, chat_id, message_id):
    if message_id is None:
        return
    conn.execute("""
        INSERT OR REPLACE INTO subscriber_messages (token_address, chat_id, message_id)
        VALUES (?, ?, ?)
    """, (token_address, chat_id, message_id))
    conn.commit()


def get_subscriber_message_id(conn, token_address, chat_id):
    row = conn.execute("""
        SELECT message_id FROM subscriber_messages
        WHERE token_address = ? AND chat_id = ?
    """, (token_address, chat_id)).fetchone()
    return row[0] if row else None