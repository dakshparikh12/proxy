"""API layer — transitive dependent of models via service -> repo."""
from orm_app.service import create


def post_user(uid: str, email: str):
    return create(uid, email)
