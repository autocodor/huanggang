# -*- coding: utf-8 -*-
"""
File Name:   ly_baoqi.py
Author :     Wave_J
Created on:  2025/11/12
Describe:    黄冈曝气智能体
"""
import sys
import os
# 获取当前文件所在目录的上级目录作为项目根目录
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, parent_dir)
import time
import numpy as np
from common.base_model import WaterControlModel
import threading
from models.unit_model import get_kalman_value_and_slope
import onnxruntime as ort


class HGBaoqiModel(WaterControlModel):
    def __init__(self):
        super().__init__(
            config_file="hg_baoqi.json",
            driver_name="HG_Baoqi",
            driver_version="v1.0.0",
            driver_desc="黄冈曝气智能体+控制日志"
        )
        # 初始化控制策略
        self.fc_model2 = ort.InferenceSession("AI_control.onnx")
        # 斜率和卡尔曼滤波
        self.m_cfng['do1_lst'] = []
        self.m_cfng['do1_km_lst'] = []
        self.m_cfng['do2_lst'] = []
        self.m_cfng['do2_km_lst'] = []

        self.m_cfng_onl_1 = {}
        self.m_cfng['do11_lst'] = []
        self.m_cfng['do12_lst'] = []
        
        self.m_cfng_onl_2 = {}
        self.m_cfng['do2_lst'] = []
        
        # 标记是否开始计时低于目标值的DO
        self.low_start_time_1 = None
        self.high_start_time_1 = None
        
        self.low_start_time_2 = None
        self.high_start_time_2 = None

        self.mqtt_out = self.config['mqtt_out']
        
        # 对每个风机进行计时
        self.fan_ids = range(1, 10)
        self.fan_run_threshold = 20.0
        self.fan_runtime_interval = self.m_cfng.get('fan_runtime_interval', 5)
        self.fan_runtime_lock = threading.Lock()
        self.fan_runtime_seconds = {fan_id: 0.0 for fan_id in self.fan_ids}
        self.fan_runtime_start = {fan_id: None for fan_id in self.fan_ids}
        self.fan_runtime_stop = {fan_id: None for fan_id in self.fan_ids}
        self.fan_runtime_state = {fan_id: False for fan_id in self.fan_ids}
        for fan_id in self.fan_ids:
            self.m_cfng[f'fj{fan_id}_runtime_seconds'] = 0.0
        self.fan_runtime_thread = threading.Thread(target=self._fan_runtime_loop)
        self.fan_runtime_thread.daemon = True
        self.fan_runtime_thread.start()
        self.fan_runtime_initialized = False
        self.switch_run_seconds = self.m_cfng['switch_time']
        for fan_id in self.fan_ids:
            self.m_cfng[f'fj{fan_id}_continuous_runtime_seconds'] = 0.0

    def _fan_runtime_loop(self):
        while True:
            try:
                self._update_fan_runtime()
            except Exception as e:
                self.logger.error(f"fan runtime record error: {e}")
            time.sleep(self.fan_runtime_interval)

    def _update_fan_runtime(self):
        now = time.time()
        with self.fan_runtime_lock:
            if not all(f'run_io{fan_id}' in self.m_cfng for fan_id in self.fan_ids):
                return  
            # 第一次执行时，根据当前电流初始化风机状态
            if not self.fan_runtime_initialized:
                for fan_id in self.fan_ids:
                    current = float(
                        self.m_cfng.get(f'run_io{fan_id}', 0) or 0
                    )
                    is_running = current > self.fan_run_threshold

                    self.fan_runtime_state[fan_id] = is_running

                    if is_running:
                        self.fan_runtime_start[fan_id] = now
                        self.fan_runtime_stop[fan_id] = None
                    else:
                        self.fan_runtime_start[fan_id] = None
                        self.fan_runtime_stop[fan_id] = now

                    self.m_cfng[f'fj{fan_id}_runtime_seconds'] = 0.0

                self.fan_runtime_initialized = True
                return
            
            for fan_id in self.fan_ids:
                is_running = float(self.m_cfng.get(f'run_io{fan_id}', 0) or 0) > self.fan_run_threshold
                was_running = self.fan_runtime_state[fan_id]

                if is_running and not was_running:
                    self.fan_runtime_start[fan_id] = now
                    self.fan_runtime_stop[fan_id] = None
                elif not is_running and was_running:
                    start_time = self.fan_runtime_start[fan_id]
                    if start_time is not None:
                        self.fan_runtime_seconds[fan_id] += now - start_time
                    self.fan_runtime_start[fan_id] = None
                    self.fan_runtime_stop[fan_id] = now

                self.fan_runtime_state[fan_id] = is_running
                start_time = self.fan_runtime_start[fan_id]
                current_runtime = self.fan_runtime_seconds[fan_id]
                if is_running and start_time is not None:
                    current_runtime += now - start_time
                self.m_cfng[f'fj{fan_id}_runtime_seconds'] = round(current_runtime, 1)
                # 计算本次连续运行时间
                continuous_runtime = 0.0

                if is_running and start_time is not None:
                    continuous_runtime = now - start_time

                self.m_cfng[f'fj{fan_id}_continuous_runtime_seconds'] = round(continuous_runtime, 1)
                
    def get_longest_continuous_running_fan(self, fan_ids):
        self._update_fan_runtime()

        with self.fan_runtime_lock:
            running_fans = {
                fan_id: self.m_cfng.get(
                    f'fj{fan_id}_continuous_runtime_seconds',
                    0
                )
                for fan_id in fan_ids
                if self.fan_runtime_state.get(fan_id)
            }

            if not running_fans:
                return None, 0

            fan_id = max(running_fans, key=running_fans.get)

            return fan_id, running_fans[fan_id]
        
    def check_fan_switch(self, fan_ids):
        stop_fan_id, run_seconds = (
            self.get_longest_continuous_running_fan(fan_ids)
        )

        # 没有正在运行的风机
        if stop_fan_id is None:
            return
        
        if run_seconds < self.switch_run_seconds:
            return

        # 选择停止时间最久的风机
        start_fan_id = self.get_earliest_stopped_fan_id(fan_ids)

        # 没有可以启动的停止风机
        if start_fan_id is None:
            return
        if start_fan_id < 4:
            txt = f"一期风机轮换：{stop_fan_id}号连续运行{run_seconds / 86400:.2f}天，准备启动{start_fan_id}号"
        else:
            txt = f"二期风机轮换：{stop_fan_id-3}号连续运行{run_seconds / 86400:.2f}天，准备启动{start_fan_id-3}号"
        self.logger.info(txt)
        self.mqtt_out['time'] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.mqtt_out["outputCommand"] = txt
        self.client.publish(self.m_cfng['mqtt_topic'], self.mqtt_out)
        

        self.m_cfng[f'fj{start_fan_id}_res'] = self.m_cfng[f'fj{stop_fan_id}_fk']
        self.client.publish(self.m_cfng['write_topic'], {
        self.devicenames[f'fj{start_fan_id}_res'] : {
            self.names[f'fj{start_fan_id}_run_cmd']: 1}
        })
        while(self.m_cfng[f'fj{start_fan_id}_gd'] < self.m_cfng['zs_min']):
            time.sleep(1)
        self.client.publish(self.m_cfng['write_topic'], {
        self.devicenames[f'fj{start_fan_id}_res'] : {
            self.names[f'fj{start_fan_id}_run_cmd']: 2,
            self.names[f'fj{start_fan_id}_res']: self.m_cfng[f'fj{start_fan_id}_res']}
        })
        
        # 确认它启动后，再停止 stop_fan_id
        self.m_cfng[f'fj{stop_fan_id}_run_cmd'] = 3
        self.m_cfng[f'fj{stop_fan_id}_res'] = 0
        self.client.publish(self.m_cfng['write_topic'], {
        self.devicenames[f'fj{stop_fan_id}_res'] : {
            self.names[f'fj{stop_fan_id}_run_cmd']: self.m_cfng[f'fj{stop_fan_id}_run_cmd'],
            self.names[f'fj{stop_fan_id}_res']: self.m_cfng[f'fj{stop_fan_id}_res']
        }
        })

    def get_fan_runtime_seconds(self, fan_id=None):
        self._update_fan_runtime()
        with self.fan_runtime_lock:
            if fan_id is None:
                return {key: self.m_cfng[f'fj{key}_runtime_seconds'] for key in self.fan_ids}
            return self.m_cfng[f'fj{fan_id}_runtime_seconds']

    def get_earliest_running_fan_id(self, fan_ids=None):
        self._update_fan_runtime()
        with self.fan_runtime_lock:
            if fan_ids is None:
                fan_ids = self.fan_ids

            running_fans = {
                fan_id: self.fan_runtime_start[fan_id]
                for fan_id in fan_ids
                if self.fan_runtime_state.get(fan_id)
                and self.fan_runtime_start.get(fan_id) is not None
            }

            if not running_fans:
                return None

            return min(running_fans, key=running_fans.get)

    def get_earliest_stopped_fan_id(self, fan_ids=None):
        self._update_fan_runtime()
        with self.fan_runtime_lock:
            if fan_ids is None:
                fan_ids = self.fan_ids

            stopped_fans = {
                fan_id: self.fan_runtime_stop[fan_id]
                for fan_id in fan_ids
                if not self.fan_runtime_state.get(fan_id)
                and self.fan_runtime_stop.get(fan_id) is not None
            }

            if not stopped_fans:
                return None

            return min(stopped_fans, key=stopped_fans.get)

    def control_loop(self, d_time, d_time2):
        self.update_status(self.m_cfng['do11_lst'], self.m_cfng['do11'], 'do11_onl', self.m_cfng_onl_1)
        self.update_status(self.m_cfng['do12_lst'], self.m_cfng['do12'], 'do12_onl', self.m_cfng_onl_1)
        
        self.update_status(self.m_cfng['do2_lst'], self.m_cfng['do2'], 'do2_onl', self.m_cfng_onl_2)

        self.logger.debug(f"DO11:{self.m_cfng_onl_1}")
        self.logger.debug(f"DO2:{self.m_cfng_onl_2}")

        if d_time >= 45:
            # 主控制逻辑
            self.logger.info("活性污泥曝气智能体控制信号：{}".format(self.m_cfng["ai"]))

            if self.m_cfng_onl_1['do11_onl']:
                self.m_cfng['do1'] = self.m_cfng['do11']
            elif self.m_cfng_onl_1['do12_onl']:
                self.m_cfng['do1'] = self.m_cfng['do12'] + self.m_cfng['do1_diff']
                self.logger.debug("一期2号生物池")
            else:
                self.m_cfng['do1'] = self.m_cfng['do1_set']
                self.logger.debug("一期2个DO异常,不控")

            if self.m_cfng_onl_2['do2_onl']:   
                self.m_cfng['do2'] = self.m_cfng['do2']
            else:
                self.m_cfng['do2'] = self.m_cfng['do2_set']
                self.logger.debug("二三期DO异常,不控")


            self.online_values2 = []
            if self.m_cfng_onl_1['do11_onl']:
                self.online_values2.append(self.m_cfng['do11'])
            if self.m_cfng_onl_1['do12_onl']:
                self.online_values2.append(self.m_cfng['do12'])
            if self.online_values2:
                self.m_cfng['do1_min_t'] = min(self.online_values2)

            # 一期控制
            self._phase_control(1, self.fc_model2)  
            # 二三期控制
            self._phase_control(2, self.fc_model2)
            
            self.check_fan_switch({1,2,3})
            self.check_fan_switch({4,5})
            self.check_fan_switch({6,7,8,9})
            
            # 一期风机启停
            runing_fj_1_count = 0   # 开始数量
            runing_fj_high_1_count = 0  # 开启并且高SV值数量
            runing_fj_low_1_count = 0   # 开启并且低SV值数量
            for i in [1,2,3]:
                if self.m_cfng[f'run_io{i}'] > 20:
                    runing_fj_1_count += 1
                    if self.m_cfng[f'fj{i}_fk'] > 95.0:
                        runing_fj_high_1_count += 1
                    if self.m_cfng[f'fj{i}_fk'] < 60.0:
                        runing_fj_low_1_count += 1
            # 增加一台风机
            if (runing_fj_1_count == 2 and self.m_cfng['do12'] < 0.6):
                if self.low_start_time_1 is None:
                    self.low_start_time_1 = time.time()
                    
                low_elapsed  = time.time() - self.low_start_time_1

                if low_elapsed >= self.m_cfng['min_time'] and runing_fj_high_1_count == 2:
                    
                    self.mqtt_out['time'] = time.strftime("%Y-%m-%d %H:%M:%S")
                    i= self.get_earliest_stopped_fan_id([1, 2, 3])
                    txt = f"一期2号生物池DO值持续低于0.6，30分钟，需增加一台风机{i}#"
                    self.mqtt_out["outputCommand"] = txt
                    self.logger.debug(txt)
                    # self.m_cfng[f'fj{i}_run_cmd'] = 1
                    self.m_cfng[f'fj{i}_res'] = float(np.clip(self.m_cfng[f'fj{i}_fk'],self.m_cfng['min1'],self.m_cfng['max1']))
                    self.client.publish(self.m_cfng['write_topic'], {
                    self.devicenames[f'fj{i}_res'] : {
                        self.names[f'fj{i}_run_cmd']: 1}
                    })
                    while(self.m_cfng[f'fj{i}_gd'] < self.m_cfng['zs_min']):
                        time.sleep(1)
                    self.client.publish(self.m_cfng['write_topic'], {
                    self.devicenames[f'fj{i}_res'] : {
                        self.names[f'fj{i}_run_cmd']: 2,
                        self.names[f'fj{i}_res']: self.m_cfng[f'fj{i}_res']}
                    })
                    self.client.publish(self.m_cfng['mqtt_topic'], self.mqtt_out)
                    self.low_start_time_1 = None
                    self.last_dcs_time2 = int(time.time())
            else:
                if self.low_start_time_1 is not None:
                    self.low_start_time_1 = None 
            
            # 减少一台风机
            if (runing_fj_1_count == 3 and self.m_cfng['do11'] > 3.5):
                if self.high_start_time_1 is None:
                    self.high_start_time_1 = time.time()
                    
                high_elapsed  = time.time() - self.high_start_time_1

                if high_elapsed >= self.m_cfng['min_time'] and runing_fj_low_1_count == 3:
                    self.mqtt_out['time'] = time.strftime("%Y-%m-%d %H:%M:%S")
                    i = self.get_earliest_running_fan_id([1, 2, 3])
                    self.mqtt_out["outputCommand"] = f"一期1号生物池DO值持续高于3.5，30分钟，需减少一台风机{i}#"
                    self.m_cfng[f'fj{i}_run_cmd'] = 3
                    self.m_cfng[f'fj{i}_res'] = 0
                    self.client.publish(self.m_cfng['write_topic'], {
                    self.devicenames[f'fj{i}_res'] : {
                        self.names[f'fj{i}_run_cmd']: self.m_cfng[f'fj{i}_run_cmd'],
                        self.names[f'fj{i}_res']: self.m_cfng[f'fj{i}_res']
                    }
                    })
                    self.client.publish(self.m_cfng['mqtt_topic'], self.mqtt_out)
                    self.high_start_time_1 = None
                    self.last_dcs_time2 = int(time.time())
                    
            else:
                if self.high_start_time_1 is not None:
                    self.high_start_time_1 = None  
                    
            # 二期风机启停
            runing_fj_2_count = 0   # 开始数量
            runing_fj_high_2_count = 0  # 开启并且高SV值数量
            runing_fj_low_2_count = 0   # 开启并且低SV值数量
            for i in [4, 5, 6, 7, 8, 9]:
                if self.m_cfng[f'run_io{i}'] > 20:
                    runing_fj_2_count += 1
                    if self.m_cfng[f'fj{i}_fk'] > 95.0:
                        runing_fj_high_2_count += 1
                    if self.m_cfng[f'fj{i}_fk'] < 60.0:
                        runing_fj_low_2_count += 1
            # 增加一台风机
            if (runing_fj_2_count == 3 and self.m_cfng['do2'] < 0.6):
                if self.low_start_time_2 is None:
                    self.low_start_time_2 = time.time()
                    
                low_elapsed  = time.time() - self.low_start_time_2

                if low_elapsed >= self.m_cfng['min_time'] and runing_fj_high_2_count == 3:
                    self.mqtt_out['time'] = time.strftime("%Y-%m-%d %H:%M:%S")
                    i = self.get_earliest_stopped_fan_id([6, 7, 8, 9])
                    self.mqtt_out["outputCommand"] = f"二期生物池DO值持续低于0.6，30分钟，需增加一台风机{i-3}#"
                    # self.m_cfng[f'fj{i}_run_cmd'] = 1
                    self.m_cfng[f'fj{i}_res'] = float(np.clip(self.m_cfng[f'fj{i}_fk'],self.m_cfng['min2'],self.m_cfng['max2']))
                    self.client.publish(self.m_cfng['write_topic'], {
                    self.devicenames[f'fj{i}_res'] : {
                        self.names[f'fj{i}_run_cmd']: 1}})
                    while(self.m_cfng[f'fj{i}_gd'] < self.m_cfng['zs_min']):
                        time.sleep(1)
                    self.client.publish(self.m_cfng['write_topic'], {
                    self.devicenames[f'fj{i}_res'] : {
                        self.names[f'fj{i}_run_cmd']: 2,
                        self.names[f'fj{i}_res']: self.m_cfng[f'fj{i}_res']
                    }
                    })
                    self.client.publish(self.m_cfng['mqtt_topic'], self.mqtt_out)
                    self.low_start_time_2 = None
                    self.last_dcs_time2 = int(time.time())
            else:
                if self.low_start_time_2 is not None:
                    self.low_start_time_2 = None 
            
            # 减少一台风机
            if (runing_fj_2_count == 4 and self.m_cfng['do2'] > 3.5):
                if self.high_start_time_2 is None:
                    self.high_start_time_2 = time.time()
                    
                high_elapsed  = time.time() - self.high_start_time_2

                if high_elapsed >= self.m_cfng['min_time'] and runing_fj_low_2_count == 4:
                    self.mqtt_out['time'] = time.strftime("%Y-%m-%d %H:%M:%S")
                    i = self.get_earliest_running_fan_id([6, 7, 8, 9])
                    self.mqtt_out["outputCommand"] = f"二期生物池DO值持续高于3.5，30分钟，需减少一台风机{i-3}#"
                    self.m_cfng[f'fj{i}_run_cmd'] = 3
                    self.m_cfng[f'fj{i}_res'] = 0
                    self.client.publish(self.m_cfng['write_topic'], {
                    self.devicenames[f'fj{i}_res'] : {
                        self.names[f'fj{i}_run_cmd']: self.m_cfng[f'fj{i}_run_cmd'],
                        self.names[f'fj{i}_res']: self.m_cfng[f'fj{i}_res']
                    }
                    })
                    self.client.publish(self.m_cfng['mqtt_topic'], self.mqtt_out)
                    self.high_start_time_2 = None
                    self.last_dcs_time2 = int(time.time())
            else:
                if self.high_start_time_2 is not None:
                    self.high_start_time_2 = None 
            
            

            if d_time2 > 100000:
                self.last_dcs_time2 = int(time.time())
                self.logger.debug(f"运行第一次，不输出")
            if self.m_cfng['write'] and self.m_cfng['control_interval'] < d_time2 < 100000:
                self.last_dcs_time2 = int(time.time())

                self.mqtt_out["outputCommand"] = ""
                self.mqtt_out['time'] = time.strftime("%Y-%m-%d %H:%M:%S")
                if self.m_cfng['ai'] == 1:
                    # mqtt_日志
                    self.mqtt_out["outputCommand"] += "一期生物池#"
                    self.mqtt_out["outputCommand"] += \
                        f"实时：一期溶解氧：{round(self.m_cfng['do1'], 2)}mg/L，目标值：{self.m_cfng['do1_set']}mg/L#"
                    for key in [1, 2, 3]:
                        if self.m_cfng[f"run_io{key}"]>20.0:  # 远程？
                            self.client.publish(self.m_cfng['write_topic'], {
                            self.devicenames[f'fj{key}_run_cmd'] : {
                                self.names[f'fj{key}_run_cmd']: 1
                            }
                            })
                            thread = threading.Thread(
                                target=self.modify_value_thread,
                                args=(self.m_cfng[f"fj{key}_fk"], self.m_cfng[f"fj{key}_res"],
                                      self.devicenames[f"fj{key}_res"], self.names[f"fj{key}_res"]),
                                kwargs={
                                    "delay": 15,
                                    "max_step": 5,
                                }
                            )
                            thread.daemon = True  # 设置为守护线程
                            thread.start()

                            self.mqtt_out["outputCommand"] += (f"一期{key}号风机运行，"
                                                               f"反馈：{round(self.m_cfng[f'fj{key}_fk'], 2)}%，"
                                                               f"上限：{self.m_cfng[f'max1']}%，"
                                                               f"下限：{self.m_cfng[f'min1']}%，"
                                                               f"控制输出：{self.m_cfng[f'fj{key}_res']}%")

                            self.logger.debug(f"{key}#风机远程、运行，反写输出结果【{self.m_cfng[f'fj{key}_res']}】")
                            
                    self.mqtt_out["outputCommand"] += "二期生物池#"
                    self.mqtt_out["outputCommand"] += \
                        f"实时：二期溶解氧：{round(self.m_cfng['do2'], 2)}mg/L，目标值：{self.m_cfng['do2_set']}mg/L#"
                    for key in [4, 5, 6, 7, 8, 9]:
                        if self.m_cfng[f"run_io{key}"]>20.0:  # 远程？
                            self.client.publish(self.m_cfng['write_topic'], {
                            self.devicenames[f'fj{key}_run_cmd'] : {
                                self.names[f'fj{key}_run_cmd']: 1
                            }
                            })
                            thread = threading.Thread(
                                target=self.modify_value_thread,
                                args=(self.m_cfng[f"fj{key}_fk"], self.m_cfng[f"fj{key}_res"],
                                      self.devicenames[f"fj{key}_res"], self.names[f"fj{key}_res"]),
                                kwargs={
                                    "delay": 15,
                                    "max_step": 5,
                                }
                            )
                            thread.daemon = True  # 设置为守护线程
                            thread.start()

                            self.mqtt_out["outputCommand"] += (f"二期{key-3}号风机运行，"
                                                               f"反馈：{round(self.m_cfng[f'fj{key}_fk'], 2)}%，"
                                                               f"上限：{self.m_cfng[f'max2']}%，"
                                                               f"下限：{self.m_cfng[f'min2']}%，"
                                                               f"控制输出：{self.m_cfng[f'fj{key}_res']}%")

                            self.logger.debug(f"{key}#风机远程、运行，反写输出结果【{self.m_cfng[f'fj{key}_res']}】")     
                            
                            
                    self.client.publish(self.m_cfng['mqtt_topic'], self.mqtt_out)

            else:
                self.logger.debug('距离上次算法输出：{}s，时间未到{}s，不输出\n'.format(d_time2, self.m_cfng['control_interval']))

            # 更新时间戳
            self.last_dcs_time = int(time.time())

    def _phase_control(self, phase_id, fc_model):
        self.logger.info(f"-----------{phase_id}期生化池曝气控制----------")
        self.m_cfng[f'do{phase_id}_km'], self.m_cfng[f'do{phase_id}_slope'] = get_kalman_value_and_slope(
            self.m_cfng[f"do{phase_id}"], self.m_cfng[f'do{phase_id}_lst'],
            self.m_cfng[f'do{phase_id}_km_lst'], 0.1, self.m_cfng["n_slope"])
        self.logger.debug(f"{phase_id}期DO:{self.m_cfng[f'do{phase_id}_km']},"
                          f"斜率：{self.m_cfng[f'do{phase_id}_slope']},"
                          f"目标值：{self.m_cfng[f'do{phase_id}_set']}")

        self.m_cfng[f'off{phase_id}_fc'] = float(fc_model.run(None, {
            'error': np.array([[self.m_cfng[f'do{phase_id}_km']-self.m_cfng[f'do{phase_id}_set']]],
                              dtype=np.float32),
            'error_diff': np.array([[self.m_cfng[f'do{phase_id}_slope']]], dtype=np.float32)
        })[0][0][0])

        if abs(self.m_cfng[f'do{phase_id}_km'] - self.m_cfng[f'do{phase_id}_set']) < self.m_cfng['do_delta']:
            self.m_cfng[f'off{phase_id}_fc'] = 0

        self.logger.debug(f"{phase_id}期do控制策略偏置：{self.m_cfng[f'off{phase_id}_fc']}")

        if (self.m_cfng[f'do{phase_id}_km'] < self.m_cfng[f'do{phase_id}_set'] -
                1.3 * self.m_cfng['max_err']):
            self.m_cfng[f'off{phase_id}_do'] = -(self.m_cfng[f'do{phase_id}_km'] - self.m_cfng[f'do{phase_id}_set']) * 3
            self.logger.debug(f"{phase_id}期DO过低偏置：{self.m_cfng[f'off{phase_id}_do']}")
        elif (self.m_cfng[f'do{phase_id}_km'] > self.m_cfng[f'do{phase_id}_set'] +
              2 * self.m_cfng['max_err']):
            self.m_cfng[f'off{phase_id}_do'] = -(self.m_cfng[f'do{phase_id}_km'] - self.m_cfng[f'do{phase_id}_set']) * 1
            self.logger.debug(f"{phase_id}期DO过高偏置：{self.m_cfng[f'off{phase_id}_do']}")
        else:
            self.m_cfng[f'off{phase_id}_do'] = 0

        if self.m_cfng['NH3_N'] > self.m_cfng['NH3_N_max']:
            self.m_cfng[f'off{phase_id}_nh3'] = (self.m_cfng['NH3_N'] - self.m_cfng['NH3_N_max']) * 4
            self.logger.debug(f"NH3过高偏置：{self.m_cfng[f'off{phase_id}_nh3']}")
        else:
            self.m_cfng[f'off{phase_id}_nh3'] = 0

        if self.m_cfng['COD'] > self.m_cfng['COD_max']:
            self.m_cfng[f'off{phase_id}_cod'] = (self.m_cfng['COD'] - self.m_cfng['COD_max'])
            self.logger.debug(f"COD过高偏置：{self.m_cfng[f'off{phase_id}_cod']}")
        else:
            self.m_cfng[f'off{phase_id}_cod'] = 0
        self.m_cfng[f'off{phase_id}'] = (self.m_cfng[f'off{phase_id}_fc'] + self.m_cfng[f'off{phase_id}_do'] +
                                         self.m_cfng[f'off{phase_id}_nh3'] + self.m_cfng[f'off{phase_id}_cod'])

        if abs(self.m_cfng[f'off{phase_id}']) < self.m_cfng['off_min']:
            self.m_cfng[f'off{phase_id}'] = 0

        if phase_id == 1:
            if self.m_cfng[f'do{phase_id}_min_t'] < 0.6:
                self.m_cfng[f'off{phase_id}'] = np.clip(self.m_cfng[f'off{phase_id}'], 1, self.m_cfng['off_max'])
                self.logger.debug(f"do{phase_id}_min_t小于0.6，偏置最低为1")
            else:
                self.m_cfng[f'off{phase_id}'] = np.clip(self.m_cfng[f'off{phase_id}'], -self.m_cfng['off_max'], self.m_cfng['off_max'])
        else:
            self.m_cfng[f'off{phase_id}'] = np.clip(self.m_cfng[f'off{phase_id}'], -self.m_cfng['off_max'], self.m_cfng['off_max'])

        self.logger.debug(f"{phase_id}期曝气总偏置：【{self.m_cfng[f'off{phase_id}']}】")

        if phase_id == 1:
            fj_id = [1, 2, 3]
        else:
            fj_id = [4, 5, 6, 7, 8, 9]
        for fan_id in (fj_id):
            self.logger.debug(f"{fan_id}#风机运行：{self.m_cfng[f'run_io{fan_id}']}，"
                              f"反馈：{self.m_cfng[f'fj{fan_id}_fk']}")
            if self.m_cfng[f"run_io{fan_id}"]>20.0:
                k = self.m_cfng[f"k{fan_id}"]
                fk = self.m_cfng[f"fj{fan_id}_fk"]
                res = fk + k * self.m_cfng[f'off{phase_id}']
                # TODO: 输出修正:过高过低变化小点

                # 输出限制和修正
                min_val = self.m_cfng[f"min{phase_id}"]
                max_val = self.m_cfng[f"max{phase_id}"]
                res = np.clip(res, min_val, max_val)  # TODO: 流量修正

                self.m_cfng[f"fj{fan_id}_res"] = round(res, 1)
                self.logger.debug(f"{fan_id}#风机输出：【{res}】")
        
if __name__ == '__main__':
    model = HGBaoqiModel()
    model.run()
