# -*- coding: utf-8 -*-
"""
File Name:   base_model.py
Author :     Wave_J
Created on:  2025/8/19
Describe:    基础模型类
"""
import time
from datetime import datetime, timezone, timedelta
from models.mqtt_demo import MQTTClient
from models.unit_model import setup_logger, read_config_safe


class WaterControlModel:
    def __init__(self, config_file, driver_name, driver_version, driver_desc):
        self.logger = setup_logger(driver_name)
        self.version = driver_version
        self.logger.info(f"名称：{driver_name}，版本：{driver_version}，描述：{driver_desc}")
        self.m_cfng = {}
        self.config = read_config_safe(config_file)
        self._load_config()
        self.client = self._connect_mqtt()
        self.last_dcs_time = 0
        self.last_dcs_time2 = 0

    def _load_config(self):
        """加载配置"""
        for k, v in self.config['basic'].items():
            self.m_cfng[k] = v
        if "fzcontrol1" in self.config:
            self.fzcontrol1 = self.config['fzcontrol1']
        if "fzcontrol2" in self.config:
            self.fzcontrol2 = self.config['fzcontrol2']

        # 提取数据点映射
        self.data_in = self.config['data_in']
        self.data_out = self.config.get('data_out', {})

        # 提取设备名称和点名称
        self.devicenames = {}
        self.names = {}
        for k, v in self.data_out.items():
            for k1, v1 in v.items():
                self.devicenames[v1] = k
                self.names[v1] = k1

        # 初始化MQTT输出
        self.mqtt_out = self.config.get('mqtt_out', {})
        self.mqtt_out["algorithmVersion"] = self.version

    def _connect_mqtt(self):
        """连接MQTT服务器"""
        current_time = str(int(time.time() * 1000))
        client = MQTTClient(
            client_id=current_time,
            broker=self.m_cfng['clientip'],
            port=self.m_cfng['clientport']
        )
        client.connect(max_retries=10, retry_interval=3)
        client.subscribe(self.m_cfng['read_topic'])
        return client

    def read_process_data(self, parse_res, d_time):
        """从MQTT读取工艺数据（通用实现）"""
        self.m_cfng['write'] = 1
        for k, v in self.data_in.items():
            for k1, v1 in v.items():
                self.m_cfng[v1] = self.client.read_data(parse_res, k, k1, "Value")
                if self.m_cfng[v1] in [None, 'NaN', 'nan']:
                    self.m_cfng['write'] = 0
                    default = 1 if v1 == "ai" else 0
                    self.m_cfng[v1] = default
                    self.logger.debug(f"异常数据[{k, k1, v1} -> {default}]")
                if d_time > 100000:
                    self.logger.debug(f"第一次数据读取[{v1} --------->[{self.m_cfng[v1]}]")

    def update_status(self, lst, value, status_key, status_dict):
        """更新状态列表和状态字典的函数"""
        status_dict[status_key] = True
        if len(lst) <= 20:
            lst.append(value)
        else:
            lst.pop(0)
        if len(lst) >= 16:
            if all(x == lst[-1] for x in lst[-15:]):
                status_dict[status_key] = False

    def send_heartbeat(self, topic, hb='ct_baoqi'):
        """发送心跳信号"""
        self.client.publish(topic, {hb: int(time.time())})

    def send_switch_status(self, topic, kg="ai_baoqi_kg", tm="collectTime"):
        """发送开关状态"""
        now_utc = datetime.now(timezone.utc)
        beijing_time = now_utc.astimezone(timezone(timedelta(hours=8)))
        formatted_time = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        self.client.publish(topic, {
            kg: self.m_cfng['ai'],
            tm: formatted_time
        })

    def modify_value_thread(self, value, target, devicename, name, delay=15, max_step=1):
        """线程函数：逐步修改设备值（支持浮点数）"""
        try:
            current = float(value)
            target = float(target)
            epsilon = 1e-2  # 浮点数比较的容差

            self.logger.debug(f"开始修改: {devicename}/{name} {current}->{target}")

            while abs(current - target) > epsilon:
                # 计算当前步长（不超过max_step）
                step = min(max_step, abs(target - current))
                # 确定方向
                if target > current:
                    new_value = current + step
                else:
                    new_value = current - step

                # 确保不会超过目标值
                if (target > current and new_value > target) or (target < current and new_value < target):
                    new_value = target

                data = {devicename: {name: new_value}}
                self.client.publish(self.m_cfng['write_topic'], data)
                self.logger.debug(f"修改成功: {data}")
                current = new_value
                time.sleep(delay)  # 确保这行与循环内的其他代码缩进一致

            self.logger.debug(f"修改完成: {devicename}/{name}={target}")
        except Exception as e:
            self.logger.error(f"修改值错误: {str(e)}")


    def write_value_once(self, target, devicename, name):
        try:
            data = {
                devicename: {
                    name: target
                }
            }

            self.client.publish(self.m_cfng['write_topic'], data)

            self.logger.debug(f"直接写入成功: {data}")

        except Exception as e:
            self.logger.error(f"直接写入错误: {str(e)}")
            
    def control_loop(self, d_time, d_time2):
        """
        主控制循环（需子类实现）
        d_time: 距离上次控制的时间差
        d_time2: 另一个时间差（根据具体需求）
        """

        raise NotImplementedError("子类必须实现 control_loop 方法")

    def run(self):
        """运行模型"""
        while True:
            try:
                d_time = int(time.time()) - self.last_dcs_time
                d_time2 = int(time.time()) - self.last_dcs_time2
                time.sleep(15)

                # 读取数据
                parse_res = self.client.parse_read_data(self.m_cfng['read_topic'])
                self.read_process_data(parse_res, d_time)

                # 执行控制逻辑
                self.control_loop(d_time, d_time2)

            except Exception as e:
                self.logger.error(f"运行错误: {e}")
                # time.sleep(60)  # 出错后等待1分钟再重试