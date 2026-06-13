"""预设系统 — 加载 YAML 预设文件"""
import os
from pathlib import Path


class Preset:
    """角色预设 — 对应 chatluna-character PresetTemplate"""

    def __init__(self, name="default", data=None):
        self.name = name
        self.system_prompt = ""
        self.input_template = ""
        self.status = ""
        self.nicknames = []
        self.mute_keywords = []
        self.config = {}

        if data:
            self._load(data)

    def _load(self, data: dict):
        self.system_prompt = data.get('system', '')
        self.input_template = data.get('input', '')
        self.status = data.get('status', '')
        self.nicknames = data.get('nick_name', [])
        self.mute_keywords = data.get('mute_keyword', [])
        self.name = data.get('name', self.name)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'system': self.system_prompt,
            'input': self.input_template,
            'status': self.status,
            'nick_name': self.nicknames,
            'mute_keyword': self.mute_keywords,
        }


class PresetLoader:
    """预设加载器 — 从目录加载 YAML 预设"""

    def __init__(self, preset_dir: str = None):
        self.preset_dir = preset_dir
        self._presets: dict[str, Preset] = {}

    def load_all(self):
        """加载所有预设"""
        self._presets.clear()
        if not self.preset_dir or not os.path.isdir(self.preset_dir):
            return
        for f in sorted(os.listdir(self.preset_dir)):
            if not f.endswith('.yml') and not f.endswith('.yaml'):
                continue
            path = os.path.join(self.preset_dir, f)
            try:
                import yaml
                with open(path, 'r', encoding='utf-8') as fp:
                    data = yaml.safe_load(fp)
                if not data or not data.get('name'):
                    continue
                name = data['name']
                self._presets[name] = Preset(name, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"加载预设 {f} 失败: {e}"
                )

    def get(self, name: str) -> Preset | None:
        return self._presets.get(name)

    def list(self) -> list[str]:
        return list(self._presets.keys())

    def get_or_default(self, name: str, fallback: Preset = None) -> Preset:
        return self._presets.get(name, fallback)
