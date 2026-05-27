__all__ = [
    "AIO_MEDIA_CLS",
    "PYRO_MEDIA_CLS",
    "build_bot_media_group",
    "build_user_media_group",
    "dynamic_send",
    "resolve_upload_target",
    "should_fallback_to_user",
]


def __getattr__(name):
    if name in {"AIO_MEDIA_CLS", "PYRO_MEDIA_CLS", "build_bot_media_group", "build_user_media_group"}:
        from . import groups

        return getattr(groups, name)
    if name == "dynamic_send":
        from .single import dynamic_send

        return dynamic_send
    if name in {"resolve_upload_target", "should_fallback_to_user"}:
        from . import targets

        return getattr(targets, name)
    raise AttributeError(name)
