import mysql.connector
from config import Config

def get_db():
    connection = mysql.connector.connect(
        host=Config.DB_HOST,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME
    )
    return connection


if __name__ == "__main__":
    try:
        db = get_db()
        print("Database connected successfully")
        db.close()
    except Exception as e:
        print("Connection failed:", e)