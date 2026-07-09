import asyncio
import websockets
import json
import os
from datetime import datetime

# Настройки админа
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "despit2024"

STORAGE_FILE = "messages.json"

def load_messages():
    if os.path.exists(STORAGE_FILE):
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_messages():
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(messages_history, f, ensure_ascii=False, indent=2)

def add_message(room, msg_data):
    if room not in messages_history:
        messages_history[room] = []
    msg_id = str(datetime.now().timestamp())
    msg_data["id"] = msg_id
    messages_history[room].append(msg_data)
    if len(messages_history[room]) > 100:
        messages_history[room] = messages_history[room][-100:]
    save_messages()
    return msg_id

messages_history = load_messages()
rooms = {"general": set()}
clients_info = {}

async def handler(websocket):
    current_room = "general"
    username = None
    is_admin = False
    
    try:
        auth_data = await asyncio.wait_for(websocket.recv(), timeout=30)
        auth = json.loads(auth_data)
        
        if auth.get("type") == "auth":
            username = auth.get("username", "Гость").strip()
            password = auth.get("password", "")
            
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                is_admin = True
                await websocket.send(json.dumps({"type": "auth_ok", "is_admin": True, "username": username}))
            elif username == ADMIN_USERNAME and password != ADMIN_PASSWORD:
                await websocket.send(json.dumps({"type": "auth_error", "message": "Неверный пароль админа"}))
                return
            else:
                is_admin = False
                await websocket.send(json.dumps({"type": "auth_ok", "is_admin": False, "username": username}))
        else:
            return
        
        clients_info[websocket] = {"username": username, "is_admin": is_admin}
        rooms[current_room].add(websocket)
        
        # Отправляем список комнат
        await websocket.send(json.dumps({
            "type": "room_list",
            "rooms": list(rooms.keys()),
            "is_admin": is_admin
        }))
        
        # Отправляем список пользователей онлайн
        await broadcast_user_list()
        
        # Отправляем историю
        history = messages_history.get(current_room, [])
        await websocket.send(json.dumps({
            "type": "history",
            "room": current_room,
            "messages": history
        }))
        
        # Системное сообщение
        sys_msg = {
            "type": "system",
            "data": f"{username} присоединился к комнате {current_room}",
            "room": current_room,
            "time": datetime.now().strftime("%H:%M:%S")
        }
        add_message(current_room, sys_msg)
        for client in rooms[current_room]:
            if client != websocket:
                await client.send(json.dumps(sys_msg))
        
        async for message in websocket:
            data = json.loads(message)
            
            # ---- ВСЕ СТАРЫЕ ТИПЫ СООБЩЕНИЙ (text, file, edit, delete, room management) ----
            if data["type"] in ["text", "file"]:
                data["room"] = current_room
                data["time"] = datetime.now().strftime("%H:%M:%S")
                data["username"] = username
                msg_id = add_message(current_room, data)
                data["id"] = msg_id
                msg = json.dumps(data)
                for client in rooms[current_room]:
                    await client.send(msg)
                        
            elif data["type"] == "edit_message":
                msg_id = data["id"]
                room = current_room
                if room in messages_history:
                    for msg_item in messages_history[room]:
                        if msg_item.get("id") == msg_id and msg_item["type"] == "text":
                            msg_item["data"] = data["data"]
                            msg_item["edited"] = True
                            save_messages()
                            break
                data["room"] = room
                for client in rooms[room]:
                    await client.send(json.dumps(data))
                    
            elif data["type"] == "delete_message":
                msg_id = data["id"]
                room = current_room
                if room in messages_history:
                    messages_history[room] = [m for m in messages_history[room] if m.get("id") != msg_id]
                    save_messages()
                data["room"] = room
                for client in rooms[room]:
                    await client.send(json.dumps(data))
                    
            elif data["type"] == "create_room":
                if not is_admin:
                    await websocket.send(json.dumps({"type": "error", "message": "Только админ может создавать комнаты"}))
                    continue
                room_name = data["name"].strip()
                if room_name and room_name not in rooms:
                    rooms[room_name] = set()
                    for client, info in clients_info.items():
                        await client.send(json.dumps({
                            "type": "room_list",
                            "rooms": list(rooms.keys()),
                            "is_admin": info["is_admin"]
                        }))
                            
            elif data["type"] == "delete_room":
                if not is_admin:
                    await websocket.send(json.dumps({"type": "error", "message": "Только админ может удалять комнаты"}))
                    continue
                room_name = data["room"].strip()
                if room_name == "general":
                    await websocket.send(json.dumps({"type": "error", "message": "Нельзя удалить общую комнату"}))
                    continue
                if room_name in rooms:
                    for client in rooms[room_name]:
                        rooms["general"].add(client)
                    del rooms[room_name]
                    if room_name in messages_history:
                        del messages_history[room_name]
                        save_messages()
                    for client, info in clients_info.items():
                        await client.send(json.dumps({
                            "type": "room_list",
                            "rooms": list(rooms.keys()),
                            "is_admin": info["is_admin"]
                        }))
                            
            elif data["type"] == "rename_room":
                if not is_admin:
                    await websocket.send(json.dumps({"type": "error", "message": "Только админ может переименовывать комнаты"}))
                    continue
                old_name = data["old_name"]
                new_name = data["new_name"].strip()
                if old_name in rooms and new_name and new_name not in rooms:
                    rooms[new_name] = rooms.pop(old_name)
                    if old_name in messages_history:
                        messages_history[new_name] = messages_history.pop(old_name)
                        save_messages()
                    for client, info in clients_info.items():
                        await client.send(json.dumps({
                            "type": "room_list",
                            "rooms": list(rooms.keys()),
                            "is_admin": info["is_admin"]
                        }))
                            
            elif data["type"] == "switch_room":
                new_room = data["room"]
                if new_room in rooms:
                    rooms[current_room].remove(websocket)
                    sys_out = {
                        "type": "system",
                        "data": f"{username} покинул комнату {current_room}",
                        "room": current_room,
                        "time": datetime.now().strftime("%H:%M:%S")
                    }
                    add_message(current_room, sys_out)
                    for client in rooms[current_room]:
                        await client.send(json.dumps(sys_out))
                    
                    current_room = new_room
                    rooms[current_room].add(websocket)
                    
                    history = messages_history.get(current_room, [])
                    await websocket.send(json.dumps({
                        "type": "history",
                        "room": current_room,
                        "messages": history
                    }))
                    
                    sys_in = {
                        "type": "system",
                        "data": f"{username} присоединился к комнате {current_room}",
                        "room": current_room,
                        "time": datetime.now().strftime("%H:%M:%S")
                    }
                    add_message(current_room, sys_in)
                    for client in rooms[current_room]:
                        if client != websocket:
                            await client.send(json.dumps(sys_in))
            
            # ---- НОВОЕ: СИГНАЛИНГ ДЛЯ ЗВОНКОВ ----
            elif data["type"] in ["call_offer", "call_answer", "ice_candidate", "call_request", "call_accepted", "call_rejected", "call_ended"]:
                target_username = data.get("target")
                if target_username:
                    # Найти websocket целевого пользователя
                    for client, info in clients_info.items():
                        if info["username"] == target_username:
                            data["from"] = username
                            await client.send(json.dumps(data))
                            break
                            
    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
        pass
    finally:
        if websocket in clients_info:
            del clients_info[websocket]
        if current_room in rooms:
            rooms[current_room].discard(websocket)
            if username:
                sys_leave = {
                    "type": "system",
                    "data": f"{username} покинул комнату {current_room}",
                    "room": current_room,
                    "time": datetime.now().strftime("%H:%M:%S")
                }
                add_message(current_room, sys_leave)
                for client in rooms[current_room]:
                    await client.send(json.dumps(sys_leave))
        await broadcast_user_list()

async def broadcast_user_list():
    users = [info["username"] for info in clients_info.values()]
    msg = json.dumps({"type": "user_list", "users": users})
    for client in clients_info:
        await client.send(msg)

async def main():
    port = int(os.environ.get("PORT", 8765))
    print(f"Сервер Despit запущен на порту {port}")
    async with websockets.serve(handler, "0.0.0.0", port):
        await asyncio.Future()

asyncio.run(main())