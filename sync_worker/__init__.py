from .runtime.state import sync_state

__all__ = ["process_master_sync", "process_json_sync", "sync_state"]


def __getattr__(name):
    if name == "process_master_sync":
        from .clone.process import process_master_sync

        return process_master_sync
    if name == "process_json_sync":
        from .json_import.process import process_json_sync

        return process_json_sync
    raise AttributeError(name)
