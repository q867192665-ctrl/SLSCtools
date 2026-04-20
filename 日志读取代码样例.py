from librouteros import connect
import time

def read_mikrotik_log(ip, username, password):
    print(f"正在连接到Mikrotik设备 {ip}...")
    try:
        # 建立连接
        api = connect(
            host=ip,
            username=username,
            password=password,
            port=29652
        )
        print("连接成功！开始实时读取日志...")
        
        # 获取初始日志
        logs = list(api("/log/print"))  # 将生成器转换为列表
        log_counter = 0  # 日志计数器，从0开始
        processed_logs = set()  # 用于存储已处理的日志，避免重复
        
        # 处理初始日志（显示所有现有日志）
        if logs:
            print("正在显示现有日志...")
            # 处理初始日志，显示并添加到已处理集合
            for log in logs:
                timestamp = log.get("time", "N/A")
                message = log.get("message", "N/A")
                topics = log.get("topics", "N/A")
                # 创建唯一标识符
                log_key = f"{timestamp}_{message}"
                if log_key not in processed_logs:
                    print(f"[{log_counter}] [{timestamp}] [{topics}] {message}")
                    log_counter += 1
                    processed_logs.add(log_key)
            print(f"现有日志显示完成，共 {len(processed_logs)} 条")
        
        # 实时读取新日志
        print("开始监控新日志...")
        while True:
            # 读取新的日志条目
            try:
                new_logs = list(api("/log/print"))  # 将生成器转换为列表
                
                # 打印新日志
                for log in new_logs:
                    timestamp = log.get("time", "N/A")
                    message = log.get("message", "N/A")
                    # 创建唯一标识符
                    log_key = f"{timestamp}_{message}"
                    
                    # 检查是否已处理过
                    if log_key not in processed_logs:
                        topics = log.get("topics", "N/A")
                        print(f"[{log_counter}] [{timestamp}] [{topics}] {message}")
                        log_counter += 1
                        processed_logs.add(log_key)
                        # 更新最后日志时间
                        last_log_time = timestamp
            except Exception as e:
                print(f"\n读取日志时出错: {str(e)}")
                # 继续执行，不要因为错误而停止
            
            # 等待一段时间后再次检查
            time.sleep(1)
            
    except Exception as e:
        print(f"错误: {str(e)}")
    finally:
        if 'api' in locals():
            api.close()
            print("连接已关闭")

if __name__ == "__main__":
    # 设备信息
    DEVICE_IP = "192.168.1.1"
    USERNAME = "admin"
    PASSWORD = "dbcom2017"
    
    # 启动日志读取
    read_mikrotik_log(DEVICE_IP, USERNAME, PASSWORD)