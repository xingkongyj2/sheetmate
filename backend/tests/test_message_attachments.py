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


def test_deleting_a_file_in_the_chat_workspace_removes_it_here_and_from_the_customer_but_not_other_conversations(client, samples):
    customer = client.post("/api/customers", json={"name": "工作区删除客户"}).json()["customer"]["id"]

    def conversation():
        return client.post("/api/conversations", json={"customer_id": customer}).json()["conversation"]["id"]

    first, second = conversation(), conversation()
    with open(samples["fees_sep"], "rb") as source:
        uploaded = client.post(f"/api/customers/{customer}/files", files={"file": (samples["fees_sep"].name, source)},
                               data={"kind": "input"}).json()["file"]
    file_id = uploaded["file_id"]

    def listed(conv_id):
        return [f["file_id"] for f in client.get(f"/api/conversations/{conv_id}").json()["conversation"]["files"]]

    assert file_id in listed(first) and file_id in listed(second)             # 客户的文件，两个会话都看得到
    assert client.delete(f"/api/files/{file_id}", params={"conversation_id": first}).status_code == 200
    assert file_id not in listed(first)                                       # 这个会话里没了
    assert file_id in listed(second)                                          # 另一个会话保留自己的引用
    assert file_id not in [f["file_id"] for f in client.get(f"/api/customers/{customer}").json()["customer"]["files"]]


def test_deleting_a_conversation_only_file_still_works_without_a_customer(client, samples):
    conv = _new_conversation(client)
    uploaded = _upload(client, conv, samples["fees_sep"])["file"]
    assert client.delete(f"/api/files/{uploaded['file_id']}", params={"conversation_id": conv}).status_code == 200
    files = client.get(f"/api/conversations/{conv}").json()["conversation"]["files"]
    assert uploaded["file_id"] not in [f["file_id"] for f in files]
