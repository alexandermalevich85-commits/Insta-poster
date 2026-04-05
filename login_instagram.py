"""One-time Instagram login script with challenge handling.

Run this script once to create a session file.
After that, the app will reuse the session without needing to re-login.
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from instagrapi import Client


def challenge_code_handler(username, choice):
    """Called when Instagram sends a verification code."""
    print(f"\nInstagram отправил код подтверждения (метод: {choice})")
    code = input("Введите код: ").strip()
    return code


def main():
    username = input("Instagram логин: ").strip() if len(sys.argv) < 2 else sys.argv[1]
    password = input("Instagram пароль: ").strip() if len(sys.argv) < 3 else sys.argv[2]

    session_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"ig_session_{username}.json",
    )

    cl = Client()
    cl.delay_range = [2, 5]
    cl.challenge_code_handler = challenge_code_handler

    print(f"\nВхожу как {username}...")

    try:
        cl.login(username, password)
        print("Вход выполнен успешно!")
    except Exception as e:
        print(f"Ошибка входа: {e}")
        print("\nПопробуйте:")
        print("1. Откройте Instagram на телефоне")
        print("2. Подтвердите вход если есть уведомление")
        print("3. Запустите этот скрипт снова")
        sys.exit(1)

    # Save session
    cl.dump_settings(session_file)
    print(f"\nСессия сохранена: {session_file}")
    print("Теперь можно публиковать через Streamlit UI.")

    # Test
    try:
        user_info = cl.account_info()
        print(f"\nАккаунт: @{user_info.username}")
        print(f"Подписчики: {user_info.follower_count}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
