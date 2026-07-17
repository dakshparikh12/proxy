"""Service layer — transitive dependent of models via repo."""
from orm_app.repo import save_user


def create(uid: str, email: str):
    return save_user(uid, email)
