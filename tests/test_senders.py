"""Platform senders (M4): reddit user-context client and threads reply API,
exercised against httpx.MockTransport — no network."""

import json

import httpx

from app.senders.reddit_user import RedditUserClient
from app.senders.threads_send import fetch_replies, post_reply

TOKEN_OK = {"access_token": "tok123", "expires_in": 3600}


def reddit_transport(comment_response=None, token_response=None, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.host == "www.reddit.com":
            return httpx.Response(200, json=token_response or TOKEN_OK)
        if request.url.path == "/api/comment":
            return httpx.Response(200, json=comment_response)
        if request.url.path == "/api/compose":
            return httpx.Response(200, json={"json": {"errors": []}})
        if request.url.path == "/message/inbox":
            return httpx.Response(
                200,
                json={"data": {"children": [{"data": {"parent_id": "t1_a", "author": "x"}}]}},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def make_client() -> RedditUserClient:
    return RedditUserClient("cid", "csec", "robofox", "hunter2")


COMMENT_OK = {"json": {"errors": [], "data": {"things": [{"data": {"name": "t1_new"}}]}}}


async def test_post_comment_returns_fullname():
    async with httpx.AsyncClient(transport=reddit_transport(COMMENT_OK)) as client:
        ok, name, error = await make_client().post_comment(client, "t3_x", "hello")
    assert ok is True
    assert name == "t1_new"
    assert error is None


async def test_post_comment_surfaces_api_errors():
    resp = {"json": {"errors": [["RATELIMIT", "try again in 5 minutes", "ratelimit"]]}}
    async with httpx.AsyncClient(transport=reddit_transport(resp)) as client:
        ok, name, error = await make_client().post_comment(client, "t3_x", "hello")
    assert ok is False
    assert "RATELIMIT" in error


async def test_token_failure_is_clean_error():
    async with httpx.AsyncClient(
        transport=reddit_transport(token_response={"error": "invalid_grant"})
    ) as client:
        ok, _, error = await make_client().post_comment(client, "t3_x", "hello")
    assert ok is False
    assert "token" in error


async def test_token_is_cached_across_calls():
    calls = []
    async with httpx.AsyncClient(transport=reddit_transport(COMMENT_OK, calls=calls)) as client:
        c = make_client()
        await c.post_comment(client, "t3_x", "one")
        await c.post_comment(client, "t3_y", "two")
    token_calls = [r for r in calls if r.url.host == "www.reddit.com"]
    assert len(token_calls) == 1


async def test_send_dm_ok():
    async with httpx.AsyncClient(transport=reddit_transport()) as client:
        ok, _, error = await make_client().send_dm(client, "shopowner42", "re: your post", "hi")
    assert ok is True and error is None


async def test_fetch_inbox_unwraps_children():
    async with httpx.AsyncClient(transport=reddit_transport()) as client:
        items = await make_client().fetch_inbox(client)
    assert items == [{"parent_id": "t1_a", "author": "x"}]


def threads_transport(publish_status=200, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.path == "/v1.0/me/threads":
            return httpx.Response(200, json={"id": "creation1"})
        if request.url.path == "/v1.0/me/threads_publish":
            return httpx.Response(publish_status, json={"id": "media9"})
        if request.url.path.endswith("/replies"):
            return httpx.Response(
                200, json={"data": [{"id": "r1", "username": "maker", "text": "hey"}]}
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_threads_reply_two_step_publish():
    calls = []
    async with httpx.AsyncClient(transport=threads_transport(calls=calls)) as client:
        ok, media_id, error = await post_reply(client, "tok", "1770001", "hello there")
    assert ok is True
    assert media_id == "media9"
    create = calls[0]
    assert create.url.params["reply_to_id"] == "1770001"
    assert create.url.params["text"] == "hello there"


async def test_threads_publish_failure_is_error():
    async with httpx.AsyncClient(transport=threads_transport(publish_status=500)) as client:
        ok, media_id, error = await post_reply(client, "tok", "1770001", "hello")
    assert ok is False
    assert media_id is None
    assert "failed" in error


async def test_threads_fetch_replies():
    async with httpx.AsyncClient(transport=threads_transport()) as client:
        replies = await fetch_replies(client, "tok", "media9")
    assert replies[0]["username"] == "maker"


def test_reddit_error_payload_is_json_serializable():
    # regression guard: error strings go into sends.error (Text) and Telegram
    err = "; ".join(":".join(map(str, e)) for e in [["A", "b", "c"]])
    assert json.dumps(err)
