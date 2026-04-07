"""
A2A中継サーバー（メッセージブローカー型）
Claude Code同士がネットワーク越しにメッセージを交換するための中継サーバー。
各Claude CodeはMCPクライアントとしてこのサーバーに接続する。

REST エンドポイント（デバッグ用）:
  GET  /         - サーバー情報
  GET  /health   - ヘルスチェック
  GET  /api/agents   - 登録エージェント一覧
  GET  /api/messages - メッセージ一覧

MCP ツール（6つ）:
  register_agent  - エージェント登録
  list_agents     - 登録済みエージェント一覧
  send_message    - メッセージ送信
  check_messages  - 未読メッセージ取得
  reply           - メッセージに返信
  get_reply       - 返信を取得
"""

import os
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP

# === インメモリDB ===
agents: dict[str, dict] = {}
messages: dict[str, dict] = {}

# === FastAPI ===
app = FastAPI(
    title="A2A Relay Server",
    description="Claude Code間メッセージ中継サーバー",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === REST エンドポイント ===

@app.get("/")
def root():
    return {
        "name": "A2A Relay Server",
        "version": "0.1.0",
        "description": "Claude Code間メッセージ中継サーバー",
        "agents_count": len(agents),
        "messages_count": len(messages),
        "mcp_endpoint": "/mcp",
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "agents": len(agents),
        "messages": len(messages),
    }

@app.get("/api/agents")
def api_agents():
    return {"agents": list(agents.values())}

@app.get("/api/messages")
def api_messages():
    return {"messages": list(messages.values())}


@app.post("/api/register")
def api_register(body: dict):
    """REST API経由でエージェント登録（MCPツールが使えない場合の代替）"""
    name = body.get("name", "")
    description = body.get("description", "")
    if not name:
        return {"error": "name is required"}
    now = datetime.now(timezone.utc).isoformat()
    agents[name] = {
        "name": name,
        "description": description,
        "registered_at": now,
        "last_seen": now,
    }
    return {"result": f"エージェント '{name}' を登録しました。現在 {len(agents)} エージェントが登録済みです。"}


@app.post("/api/send")
def api_send(body: dict):
    """REST API経由でメッセージ送信（MCPツールが使えない場合の代替）"""
    to = body.get("to", "")
    message = body.get("message", "")
    from_agent = body.get("from_agent", "anonymous")
    if not to or not message:
        return {"error": "to and message are required"}
    if to not in agents:
        available = ", ".join(agents.keys()) if agents else "なし"
        return {"error": f"エージェント '{to}' は未登録です。登録済み: {available}"}
    if from_agent in agents:
        agents[from_agent]["last_seen"] = datetime.now(timezone.utc).isoformat()
    msg_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    messages[msg_id] = {
        "id": msg_id,
        "from_agent": from_agent,
        "to_agent": to,
        "message": message,
        "timestamp": now,
        "status": "unread",
        "reply": None,
        "reply_timestamp": None,
    }
    return {"result": f"メッセージ送信完了。message_id: {msg_id}", "message_id": msg_id}


@app.get("/api/check/{agent_name}")
def api_check(agent_name: str):
    """REST API経由で未読メッセージ取得"""
    if agent_name in agents:
        agents[agent_name]["last_seen"] = datetime.now(timezone.utc).isoformat()
    unread = [
        m for m in messages.values()
        if m["to_agent"] == agent_name and m["status"] == "unread"
    ]
    for m in unread:
        m["status"] = "read"
    return {"unread_count": len(unread), "messages": unread}

# === MCP サーバー ===

mcp_server = FastMCP(
    "A2A Relay",
    instructions=(
        "エージェント間メッセージ中継サーバーです。"
        "register_agentで自分を登録し、send_messageで他のエージェントにメッセージを送り、"
        "check_messagesで自分宛のメッセージを確認し、replyで返信できます。"
        "日本語で回答してください。"
    ),
)


@mcp_server.tool
def register_agent(name: str, description: str = "") -> str:
    """エージェントを登録する。セッション開始時に1回呼び出す。"""
    now = datetime.now(timezone.utc).isoformat()
    agents[name] = {
        "name": name,
        "description": description,
        "registered_at": now,
        "last_seen": now,
    }
    return f"エージェント '{name}' を登録しました。現在 {len(agents)} エージェントが登録済みです。"


@mcp_server.tool
def list_agents() -> str:
    """登録済みエージェントの一覧を取得する。"""
    if not agents:
        return "登録済みエージェントはありません。"
    lines = []
    for a in agents.values():
        lines.append(f"- {a['name']}: {a['description']} (登録: {a['registered_at']})")
    return "\n".join(lines)


@mcp_server.tool
def send_message(to: str, message: str, from_agent: str = "anonymous") -> str:
    """指定エージェントにメッセージを送信する。返り値のmessage_idでget_replyを呼べる。"""
    if to not in agents:
        available = ", ".join(agents.keys()) if agents else "なし"
        return f"エラー: エージェント '{to}' は未登録です。登録済み: {available}"

    # 送信元のlast_seen更新
    if from_agent in agents:
        agents[from_agent]["last_seen"] = datetime.now(timezone.utc).isoformat()

    msg_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    messages[msg_id] = {
        "id": msg_id,
        "from_agent": from_agent,
        "to_agent": to,
        "message": message,
        "timestamp": now,
        "status": "unread",
        "reply": None,
        "reply_timestamp": None,
    }
    return f"メッセージ送信完了。message_id: {msg_id} (宛先: {to})"


@mcp_server.tool
def check_messages(agent_name: str) -> str:
    """自分宛の未読メッセージを取得する。取得後、ステータスをreadに変更する。"""
    if agent_name in agents:
        agents[agent_name]["last_seen"] = datetime.now(timezone.utc).isoformat()

    unread = [
        m for m in messages.values()
        if m["to_agent"] == agent_name and m["status"] == "unread"
    ]
    if not unread:
        return f"'{agent_name}' 宛の未読メッセージはありません。"

    lines = []
    for m in unread:
        m["status"] = "read"
        lines.append(
            f"[{m['id']}] {m['from_agent']}から ({m['timestamp']}):\n{m['message']}"
        )
    return f"未読メッセージ {len(unread)} 件:\n\n" + "\n\n---\n\n".join(lines)


@mcp_server.tool
def reply(message_id: str, response: str) -> str:
    """メッセージに返信する。message_idはcheck_messagesで取得したIDを指定する。"""
    if message_id not in messages:
        return f"エラー: メッセージID '{message_id}' が見つかりません。"

    msg = messages[message_id]
    if msg["reply"] is not None:
        return f"エラー: メッセージID '{message_id}' は既に返信済みです。"

    msg["reply"] = response
    msg["reply_timestamp"] = datetime.now(timezone.utc).isoformat()
    return f"返信完了。(message_id: {message_id}, 宛先: {msg['from_agent']})"


@mcp_server.tool
def get_reply(message_id: str) -> str:
    """送信したメッセージへの返信を取得する。"""
    if message_id not in messages:
        return f"エラー: メッセージID '{message_id}' が見つかりません。"

    msg = messages[message_id]
    if msg["reply"] is None:
        return f"メッセージID '{message_id}' への返信はまだありません。(宛先: {msg['to_agent']}, 送信: {msg['timestamp']})"

    return (
        f"返信あり (message_id: {message_id}):\n"
        f"元メッセージ ({msg['from_agent']}→{msg['to_agent']}): {msg['message']}\n"
        f"返信 ({msg['to_agent']}→{msg['from_agent']}): {msg['reply']}\n"
        f"返信日時: {msg['reply_timestamp']}"
    )


# === MCP マウント ===
mcp_app = mcp_server.http_app(transport="sse")
app.mount("/mcp", mcp_app)
