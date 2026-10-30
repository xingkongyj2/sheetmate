import pytest

from tests.test_api import _chat, _new_conversation, _upload


@pytest.mark.parametrize("plan_steps", [None, [{"id": 1, "description": "查看附件"}]])
def test_uploaded_files_remain_associated_with_user_message(client, samples, fake_llm, plan_steps):
    conversation = _new_conversation(client)
    uploaded = [
        _upload(client, conversation, samples["fees_sep"])["file"],
        _upload(client, conversation, samples["expected_sep"], kind="expected")["file"],
    ]
    ids = [file["file_id"] for file in uploaded]
    fake_llm()
    _chat(client, conversation, "查看上传的表格", attachments=ids, plan_steps=plan_steps)

    detail = client.get(f"/api/conversations/{conversation}").json()["conversation"]
    message = next(message for message in detail["messages"] if message["role"] == "user")
    assert message["meta"]["attachments"] == ids
    assert {file["file_id"]: file["filename"] for file in detail["files"]} == {
        file["file_id"]: file["filename"] for file in uploaded
    }
    for file in uploaded:
        assert client.get(f"/api/files/{file['file_id']}/preview").status_code == 200
        assert client.get(file["download_url"]).status_code == 200

    assert client.delete(f"/api/files/{ids[0]}").status_code == 200
    detail = client.get(f"/api/conversations/{conversation}").json()["conversation"]
    message = next(message for message in detail["messages"] if message["role"] == "user")
    assert message["meta"]["attachments"] == ids
    assert ids[0] not in [file["file_id"] for file in detail["files"]]
