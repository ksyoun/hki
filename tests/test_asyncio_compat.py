"""asyncio_compat helpers."""

from hki.asyncio_compat import _is_benign_connection_lost


def test_benign_connection_lost_proactor_callback():
    assert _is_benign_connection_lost(
        {
            "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
            "exception": ConnectionResetError(10054, "reset"),
        }
    )


def test_benign_connection_lost_without_exception():
    assert _is_benign_connection_lost(
        {
            "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
        }
    )


def test_other_exceptions_not_benign():
    assert not _is_benign_connection_lost(
        {
            "message": "Task exception was never retrieved",
            "exception": ValueError("boom"),
        }
    )
