"""Persistence layer — writes the users table (direct dependent of models)."""
from orm_app.models import User


def save_user(uid: str, email: str) -> User:
    user = User(uid, email)
    # writes to the users table
    return user
