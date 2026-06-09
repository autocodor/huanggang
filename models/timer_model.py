# -*- coding: utf-8 -*-
"""
File Name:   timer_model.py
Author :     Wave_J
Created on:  2025/5/23
Describe:    计时器
"""
import time

class TimeCount:
    def __init__(self):
        self.current_time = 0  # 初始设定值
        self.timer_flag = 0
        self.last_timer = int(time.time())

    def put_value(self, io_start):
        if not io_start == self.timer_flag:  # 标签点位变更
            if io_start == 0:
                self.timer_flag = 0
            else:
                self.timer_flag = 1
                self.last_timer = int(time.time())

    def get_value(self):
        if self.timer_flag == 1:
            self.current_time = int(time.time()) - self.last_timer
        else:
            self.current_time = 0

        return self.current_time


if __name__ == '__main__':
    # print(int(time.time()))
    m = TimeCount()
    # m.put_value(0)
    time.sleep(3)
    print(m.get_value())

    time.sleep(1)
    m.put_value(1)
    time.sleep(2)
    print(m.get_value())

    # m.put_value(0)
    time.sleep(2)
    print('开启后计算占用时间:',m.get_value())

    m.put_value(0)
    time.sleep(1)
    print(m.get_value())

    time.sleep(3)
    print('关闭后计算占用时间:',m.get_value())
