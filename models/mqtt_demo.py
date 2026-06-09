# -*- coding: utf-8 -*-
"""
File Name:   mqtt_demo.py
Author :     Wave_J
Created on:  2025/7/11
Describe:
"""
import paho.mqtt.client as mqtt
import ssl
import threading
import json
from models.unit_model import setup_logger
import time

class MQTTClient:
    def __init__(self, client_id, broker, port=1883, keepalive=60, log_file=None):
        """
        初始化MQTT客户端
        :param client_id: 客户端唯一标识
        :param broker: 代理服务器地址
        :param port: 端口号（默认1883）
        :param keepalive: 心跳间隔（秒）
        """
        self.client = mqtt.Client(client_id = client_id)
        self.broker = broker
        self.port = port
        self.keepalive = keepalive
        # 配置回调函数
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe
        self.client.on_publish = self._on_publish
        self.latest_data = {}
        self._lock = threading.RLock()  # 创建可重入锁
        self.subscribed_topics = []  # 新增：保存订阅主题
        self.connected = False  # 连接状态标志
        if log_file:
            self.logger = setup_logger(log_file)
        else:
            self.logger = setup_logger(self.__class__.__name__)

    def connect(self, username=None, password=None, tls=None, max_retries=5, retry_interval=5):
        retry_count = 0
        while retry_count < max_retries:
            try:
                # 清理旧连接
                self.client.disconnect()

                if username and password:
                    self.client.username_pw_set(username, password)

                if tls and tls.get('ca_certs'):
                    self.client.tls_set(
                        ca_certs=tls['ca_certs'],
                        certfile=tls.get('certfile'),
                        keyfile=tls.get('keyfile'),
                        tls_version=ssl.PROTOCOL_TLSv1_2
                    )

                self.client.connect(self.broker, self.port, self.keepalive)
                self.client.loop_start()
                print(f"Connection attempt {retry_count + 1}/{max_retries}")
                return True
            except (ConnectionRefusedError, OSError) as e:
                self.logger.error(f"Broker unavailable: {str(e)}, retrying in {retry_interval}s...")
                retry_count += 1
                time.sleep(retry_interval)
        self.logger.error(f"Failed to connect after {max_retries} attempts")
        return False

    def subscribe(self, topic, qos=0):
        """
        订阅主题
        :param topic: 支持字符串或列表形式订阅多个主题
        :param qos: 服务质量等级（0/1/2）
        """
        try:
            if isinstance(topic, list):
                topics = [(t, qos) for t in topic]
                self.client.subscribe(topics)
            else:
                self.client.subscribe(topic, qos)
        except Exception as e:
            self.logger.error(f"Subscribe error: {str(e)}")
        self.subscribed_topics.append((topic, qos))  # 记录订阅

    def publish(self, topic, payload, qos=0, retain=False):
        """
        发布消息
        :param topic: 目标主题
        :param payload: 消息内容（支持字典自动转换）
        :param qos: 服务质量等级
        :param retain: 保留消息标志
        """
        try:
            if isinstance(payload, dict):
                payload = json.dumps(payload)
            self.client.publish(topic, payload, qos=qos, retain=retain)
            self.logger.debug(f"Published: {topic} - {payload}")
        except Exception as e:
            self.logger.error(f"Publish error: {str(e)}")

    def disconnect(self):
        """断开连接"""
        self.client.loop_stop()
        self.client.disconnect()
        self.logger.debug("Disconnected from broker")

    def parse_read_data(self, in_topic):
        result_dict = {}
        try:
            with self._lock:  # 加锁读取数据
                mqtt_data = self.latest_data.get(in_topic, [])  # 获取数据，默认为空列表
            for item in mqtt_data:
                device = item.get("DeviceName")
                name = item.get("Name")
                # 创建设备层字典（如果不存在）
                if device not in result_dict:
                    result_dict[device] = {}

                # 创建数据点层字典（完整保留原始数据结构）
                result_dict[device][name] = {"Value": item.get("Value")
                }
            return result_dict
        except Exception as e:
            self.logger.error(f"parse data error: {str(e)}")
            return result_dict

    def read_data(self, sub_data, DeviceName, Name, Value="Value"):
        """读取所有主题的最新数据"""
        try:
            is_success = sub_data[DeviceName][Name][Value]
            return is_success
        except Exception as e:
            self.logger.error(f"Read data error: {str(e)}")
            return None

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            self.logger.debug(f"Connected to {self.broker} successfully")
            for topic, qos in self.subscribed_topics:
                self.client.subscribe(topic, qos)
        else:
            self.connected = False
            self.logger.debug(f"Connection failed with code {rc}")

    def read_AI(self, in_topic):
        #  in_topic  配置文件中的主题
        with self._lock:  # 加锁读取数据
            result_dict = self.latest_data.get(in_topic, [])  # 获取数据，默认为空列表
        return result_dict

    def _on_message(self, client, userdata, msg):
        """消息到达回调（自动按主题分类存储）"""
        try:
            # 解析JSON数据
            payload = json.loads(msg.payload.decode("utf-8"))
            with self._lock:  # 加锁更新数据
                # 按主题名存储到字典（自动创建/更新条目）
                self.latest_data[msg.topic] = payload


        except json.JSONDecodeError:
            # 非JSON数据直接存储原始内容
            with self._lock:  # 加锁更新数据
                self.latest_data[msg.topic] = msg.payload.decode("utf-8")
            self.logger.debug(f"收到非JSON数据 @ {msg.topic}")
        except Exception as e:
            self.logger.error(f"数据处理失败: {str(e)}")

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        """订阅确认回调"""
        self.logger.debug(f"Subscribed with QOS: {granted_qos}")
    def _on_publish(self, client, userdata, mid):
        """发布确认回调"""
        self.logger.debug(f"Message published (mid={mid})")
