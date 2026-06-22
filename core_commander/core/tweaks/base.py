# -*- coding: utf-8 -*-
from typing import Dict, Type

class BaseTweak:
    """
    基类接口：所有系统优化项（Tweak）必须继承该类并实现 apply 方法。
    """
    @property
    def id(self) -> str:
        raise NotImplementedError("Each tweak must define a unique string ID")
        
    def apply(self, enable: bool) -> bool:
        """
        执行具体的优化或还原逻辑
        :param enable: True 代表开启优化，False 代表还原默认值
        :return: bool 是否成功
        """
        raise NotImplementedError

class TweakRegistry:
    """
    系统优化项注册中心：负责动态加载和管理所有具体的 Tweak 实现。
    """
    _tweaks: Dict[str, BaseTweak] = {}
    
    @classmethod
    def register(cls, tweak_cls: Type[BaseTweak]):
        """
        类装饰器：用于自动实例化并注册 Tweak
        """
        inst = tweak_cls()
        cls._tweaks[inst.id] = inst
        return tweak_cls

    @classmethod
    def get(cls, tweak_id: str) -> BaseTweak:
        return cls._tweaks.get(tweak_id)
        
    @classmethod
    def get_all(cls) -> Dict[str, BaseTweak]:
        return cls._tweaks
