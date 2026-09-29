"""Explicit, account-bound Codex continuation; an uncertain head cannot fork."""

from pathlib import Path

from ..contracts import ProviderFault, ProviderResponse


def prepare_session(request, directory: Path, profile, revision, *, read_json, write_json):
    identity = request.session.id
    root = directory / "sessions" / identity
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "session.json"
    binding = {"version": 1, "id": identity, "model": request.model, "reasoning_effort": request.reasoning_effort,
               "account": {"profile": profile, "revision": revision}}

    def reject():
        raise ProviderFault("uncertain", "The audit session needs explicit receipt/account reconciliation.")

    if request.web_search or request.request_id.startswith(("news-", "quant-feed-")):
        reject()
    if path.exists():
        try:
            state = read_json(path.read_bytes())
            if any(state.get(key) != value for key, value in binding.items()):
                reject()
        except (ValueError, TypeError, AttributeError):
            reject()
    else:
        if request.session.previous_request_id is not None:
            reject()
        state = {**binding, "head": None, "thread_id": None, "inflight": None, "usage": {}}
    previous = request.session.previous_request_id
    if previous:
        receipt_path = directory / (previous + ".json")
        try:
            receipt = read_json(receipt_path.read_bytes())
            result = ProviderResponse.model_validate(receipt["result"])
            if (receipt.get("state") != "complete" or receipt.get("request_id") != previous
                    or receipt.get("account") != binding["account"]
                    or receipt.get("session", {}).get("id") != identity
                    or receipt.get("requested_execution") != {
                        "model": request.model, "reasoning_effort": request.reasoning_effort}
                    or result.request_id != previous or not result.thread_id
                    or not isinstance(receipt.get("session_usage"), dict)):
                reject()
        except (OSError, ValueError, TypeError, KeyError):
            reject()
        if state["inflight"] == previous:
            # Recover only the receipt/session commit window, never model inference.
            state.update(head=previous, thread_id=result.thread_id, inflight=None, usage=receipt["session_usage"])
            write_json(path, state)
        if state["head"] != previous or state["thread_id"] != result.thread_id:
            reject()
        state["usage"] = receipt["session_usage"]
    elif state["head"] is not None:
        reject()
    if state["inflight"] not in (None, request.request_id):
        reject()
    work = root / "work"
    work.mkdir(exist_ok=True, mode=0o700)
    return path, state, work


def turn_usage(cumulative, previous):
    """CLI 0.154.0 resume reports session totals, including earlier paid turns."""
    if not {"input_tokens", "output_tokens"} <= cumulative.keys():
        raise ProviderFault("uncertain", "Codex did not report bounded session usage.")
    delta = {}
    for key in cumulative.keys() | previous.keys():
        current, before = cumulative.get(key, 0), previous.get(key, 0)
        if type(current) is not int or type(before) is not int or not 0 <= before <= current:
            raise ProviderFault("uncertain", "Codex session usage needs reconciliation.")
        delta[key] = current - before
    if delta.get("cached_input_tokens", 0) > delta["input_tokens"]:
        raise ProviderFault("uncertain", "Codex session cache usage needs reconciliation.")
    return delta
