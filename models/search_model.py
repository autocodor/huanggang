# -*- coding: utf-8 -*-

"""
@author: yora zhang
@desc: 2022/11/2-module_search.py 查表,按表查询相关参数
"""
class Search:
    def __init__(self):
        self.dict_data = {}

    def put_value(self, key, value):
        self.dict_data.update({key: value})  # 增加字典中的值

    def search_value(self, key):
        if self.dict_data:
            for i in self.dict_data.keys():
                if float(key) <= float(i):
                    return self.dict_data[i]

            # print("last key : ", list(self.dict_data.keys())[-1]) # 获取最后一个元素的key
            # print("last value : ", self.dict_data.get(list(self.dict_data.keys())[-1])) # 获取最后一个元素的值

            return self.dict_data.get(list(self.dict_data.keys())[-1])
        else:
            return None # 错误代码

    # def get_value(self):
    #     return self.current_data




if __name__ == '__main__':
    from model.unit_model import setup_logger, read_config_safe
    from datetime import datetime

    m = Search()
    m_cfng = {}  # 配置文件变量
    config = read_config_safe("D:\coding\watercontrol\jl_lift.json")
    PV_set = config['PV_set']
    print('PV_set:', PV_set)
    for key, value in PV_set.items():
        m.put_value(key, value)

    print(datetime.now().month%2)
    print(m.search_value(datetime.now().hour))

    # 计算值应为8.5
    print("Hello World!")
