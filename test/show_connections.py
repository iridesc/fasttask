import redis
import time

while True:
    with redis.Redis(host="localhost", port=6379, password="passwd") as r:
        connections = r.info(section="clients")["connected_clients"]
        client_list_output = r.client_list()

    ip_to_info = dict()
    for client_info in client_list_output:
        ip, port = client_info.get("addr").split(":")
        
        ip_to_info.setdefault(ip, dict())
        ip_info = ip_to_info[ip]

        ip_info.setdefault(port, dict())

        connection_info = ip_info[port]
        connection_info["idle"] = client_info.get("idle")
        connection_info["cmd"] = client_info.get("cmd")

    print(ip_to_info)
    print(f"当前连接数: {connections}")
    time.sleep(3)
