import jarvis_bridge
jarvis_bridge.check_ollama = lambda: True
from jarvis_bridge import _chat_memory, app, init

jarvis_bridge.call_ollama = lambda sys_prompt, msgs: "mocked reply"
init()

def test_reverse():
    with app.test_client() as client:
        # Preetam -> Jarvis
        client.post("/personality/use", json={"chat_id": "chatY", "personality": "preetam_v1"})
        client.post("/chat", json={"chat_id": "chatY", "text": "start chat", "sender": "user_y"})
        mem1 = id(_chat_memory["chatY"])
        
        client.post("/personality/use", json={"chat_id": "chatY", "personality": "jarvis"})
        mem2 = id(_chat_memory["chatY"])
        
        client.post("/chat", json={"chat_id": "chatY", "text": "Who are you now?", "sender": "user_y"})
        mem3_msgs = [m["content"] for m in _chat_memory["chatY"].messages]
        
        print(f"Reverse Test -> Mem1 != Mem2: {mem1 != mem2}")
        print(f"Reverse Test -> Mem3 msgs: {mem3_msgs}")

def test_isolation():
    with app.test_client() as client:
        client.post("/chat", json={"chat_id": "chatZ", "text": "isolation test", "sender": "user_z"})
        memZ_before = id(_chat_memory["chatZ"])
        
        client.post("/personality/use", json={"chat_id": "chatY", "personality": "preetam_v1"})
        
        memZ_after = id(_chat_memory["chatZ"])
        print(f"Isolation Test -> memZ_before == memZ_after: {memZ_before == memZ_after}")

test_reverse()
test_isolation()
