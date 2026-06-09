# -*- coding: utf-8 -*-
"""
File Name:   unit_model.py
Author :     Wave_J
Created on:  2025/7/14
Describe:    
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any, Union
import json
from pykalman import KalmanFilter
import numpy as np
from scipy.stats import linregress


def fun_kalman(observations, damping=1.0):
    # To return the smoothed time series data
    observation_covariance = damping
    initial_value_guess = observations[0]
    transition_matrix = 1
    transition_covariance = 0.1
    # initial_value_guess
    kf = KalmanFilter(
        initial_state_mean=initial_value_guess,
        initial_state_covariance=observation_covariance,
        observation_covariance=observation_covariance,
        transition_covariance=transition_covariance,
        transition_matrices=transition_matrix
    )
    pre_state, state_cov = kf.smooth(observations)
    return pre_state


# 通用函数 ： 计算卡尔曼滤波后的值和斜率
def get_kalman_value_and_slope(value, origin_list, filter_list, damping, n_slope):
    slope = 0
    origin_list.append(value)
    kalman_list = fun_kalman(origin_list, damping)
    kalman_value = round(kalman_list[-1][0], 4)
    filter_list.append(kalman_value)
    if len(origin_list) > n_slope:
        origin_list.pop(0)
        filter_list.pop(0)
    if len(filter_list) > n_slope - 1:
        x = np.arange(0, len(filter_list))
        slope, intercept, r_value, p_value, std_err = linregress(x, filter_list)
        slope = round(slope, 5)
    return kalman_value, slope


# 通用函数 ： 计算反馈值的近几个点的均值
def get_feedback_avg(value, origin_list, n_feedback):
    if not value == 'null':
        origin_list.append(value)
        if len(origin_list) > n_feedback:
            origin_list.pop(0)
        feedback_avg = round(sum(origin_list) / len(origin_list), 2)
        return feedback_avg


# 通用函数 ： 计算偏置量
def calc_km_offset(c2_value, c2_target, c2_slope, c1_slope, is_accord_c2, is_accord_c1
                   , c2_slope_offset, c1_slope_offset, m_km_dict, log):
    try:
        log.debug('----------运行参数表----------')
        v_offset = 0  # 偏置量平稳上升时0
        v_mld = 0  # 出口快速上升时
        v_md = 0  # 出口快速下降时
        v_mu = 0  # 入口NOX斜率波动较大时的偏置量
        v_mhu = 0  # 出口平稳下降时
        var = round(c2_value - c2_target, 2)
        log.debug("目标值：{}，误差：{}".format(c2_target, var))
        len0 = len(m_km_dict)  # q_21表示NOX质量
        if var <= m_km_dict['1']['min']:
            v_offset = m_km_dict['1']['value']  # 第一列 平稳状态
            v_mld = m_km_dict['1']['mld']  # 第二列 出口快速上升
            v_mhu = m_km_dict['1']['mhu']  # 最后一列 平稳下降
            v_md = m_km_dict['1']['md']  # 第三例 出口快速下降
            v_mu = m_km_dict['1']['mu']  # 倒数第二列 入口变化不论上升下降
        elif var > m_km_dict[str(len0)]['max']:
            v_offset = m_km_dict[str(len0)]['value']
            v_mld = m_km_dict[str(len0)]['mld']
            v_mhu = m_km_dict[str(len0)]['mhu']
            v_md = m_km_dict[str(len0)]['md']
            v_mu = m_km_dict[str(len0)]['mu']
        else:
            for i in range(1, len0 + 1):
                if m_km_dict[str(i)]['min'] < var <= m_km_dict[str(i)]['max']:
                    v_offset = m_km_dict[str(i)]['value']
                    v_mld = m_km_dict[str(i)]['mld']
                    v_mhu = m_km_dict[str(i)]['mhu']
                    v_md = m_km_dict[str(i)]['md']
                    v_mu = m_km_dict[str(i)]['mu']
                    break
        # 如果斜率处于平稳状态，则获取平稳状态的斜率
        if is_accord_c2:
            if c2_slope > c2_slope_offset:
                v_offset = v_mld
                log.info("出口快速上升状态，偏置增量v_mld：{0}".format(v_offset))
            elif c2_slope < - c2_slope_offset:
                v_offset = v_md
                log.info("出口快速下降状态，偏置增量v_md：{0}".format(v_offset))
            elif c2_slope >= 0:
                log.info("出口平稳上升状态，偏置增量：{0}".format(v_offset))
            elif c2_slope < 0:
                v_offset = v_mhu
                log.info("出口平稳下降状态，偏置增量v_mhu：{0}".format(v_offset))
        # 根据入口NOX斜率进行计算
        if is_accord_c1:
            var2 = 0
            if c1_slope > c1_slope_offset:
                var2 = v_mu
            elif c1_slope < - c1_slope_offset:
                var2 = -v_mu
            if not var2 == 0:
                log.info("入口浓度波动，偏置增量为：{0}".format(var2))
            v_offset += var2
            log.info("偏置增量总取值：{:.2f}".format(v_offset))
    except Exception as err:
        v_offset = 0
        log.error('[ERROR-set_offset]:%s' % (err,))
    return v_offset


def output_limiting(plan_value, last_value, no_adjust_range, limit_step, log):
    actual_value = plan_value
    if abs(plan_value - last_value) < no_adjust_range:
        actual_value = last_value
        log.debug("计算值与反馈值相差小于{}，不做调整".format(no_adjust_range))
    elif plan_value - last_value > limit_step:
        actual_value = last_value + limit_step
        log.debug("开度偏置高，限幅{}".format(limit_step))
    elif plan_value - last_value < -limit_step:
        actual_value = last_value - limit_step
        log.debug("开度偏置低，限幅-{}".format(limit_step))
    return actual_value


def setup_logger(name):
    # 创建log目录（如果不存在）
    log_dir = 'log'
    os.makedirs(log_dir, exist_ok=True)

    # 创建logger实例
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 创建文件处理器（自动按大小滚动）
    file_handler = RotatingFileHandler(
        f'{log_dir}/{name}.log',
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=20,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)

    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)

    # 设置日志格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(lineno)d - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # 添加处理器
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

def read_config_safe(config_path: Union[str, Path]) -> Dict[str, Any]:
    """
    安全读取JSON配置文件，包含错误处理机制
    :param config_path: 配置文件路径（支持字符串或Path对象）
    :raises FileNotFoundError: 配置文件不存在时抛出
    :raises json.JSONDecodeError: JSON格式错误时抛出
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"配置文件 {config_path} 未找到") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON解析错误：{e.msg}  (行{e.lineno} 列{e.colno})") from e

