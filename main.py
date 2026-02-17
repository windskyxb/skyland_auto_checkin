#!/usr/bin/env python3
# coding=utf-8
"""
cron: 31 8 * * *
new Env('森空岛自动签到');
Author: devnak
Update: 2026/2/17
"""
import hashlib
import hmac
import json
import os
import re
import time
from urllib import parse

# 账号等待间隔（秒）
ACCOUNT_INTERVAL = 1
# 请求超时时间（秒）
REQUEST_TIMEOUT = 9
# HTTP 重试次数
HTTP_RETRY_TIMES = 3

# 接口地址
BINDING_URL = "https://zonai.skland.com/api/v1/game/player/binding"
CRED_CODE_URL = "https://zonai.skland.com/api/v1/user/auth/generate_cred_by_code"
GRANT_CODE_URL = "https://as.hypergryph.com/user/oauth2/v2/grant"
APP_CODE = "4ca99fa6b56cc2ba"

# 请求头配置
USER_AGENT = {
    "User-Agent": "Skland/1.0.1 (com.hypergryph.skland; build:100001014; Android 31; ) Okhttp/4.11.0"
}

# 签名请求头模板
SIGN_HEADER_TPL = {
    "platform": "",
    "timestamp": "",
    "dId": "",
    "vName": ""
}

# 游戏配置
GAME_CONFIG = {
    "arknights": {
        "name": "明日方舟",
        "checkin_url": "https://zonai.skland.com/api/v1/game/attendance",
        "app_code": "arknights",
    },
    "endfield": {
        "name": "明日方舟：终末地",
        "checkin_url": "https://zonai.skland.com/api/v1/game/endfield/attendance",
        "app_code": "endfield",
    },
}

class Config:
    """封装配置参数"""

    def __init__(self, tokens=None, enable_notify=False):
        self.tokens = tokens if tokens is not None else []
        self.enable_notify = enable_notify

    @classmethod
    def from_env(cls):
        """从环境变量加载配置"""
        tokens_env = os.getenv("SKYLAND_TOKEN", "")
        notify_env = os.getenv("SKYLAND_NOTIFY", "")
        tokens = [t.strip() for t in tokens_env.split(";") if t.strip()]
        enable_notify = notify_env.strip().lower() == "true"
        return cls(tokens=tokens, enable_notify=enable_notify)


def create_session():
    """创建带重试机制的 HTTP 会话实例"""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=HTTP_RETRY_TIMES,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


def _get_display_width(string):
    """计算字符串的显示宽度（中文字符宽度为 2，其它为 1）"""
    width = 0
    for char in string:
        if "\u4e00" <= char <= "\u9fff" or "\u3000" <= char <= "\u303f":
            width += 2
        else:
            width += 1
    return width


def _pad_to_width(string, target_width):
    """将字符串填充到指定显示宽度"""
    current_width = _get_display_width(string)
    padding = max(0, target_width - current_width)
    return string + " " * padding


def _build_msg(game_name, role_name, channel, result):
    """构建签到结果消息"""
    game = _pad_to_width(f"[{game_name}]", 18)
    role_info = _pad_to_width(f"{role_name}（{channel}）", 32)
    return f"{game}\t{role_info}\t结果：{result}"


class SkylandCheckin:
    """
    森空岛自动签到核心类
    封装签到相关的所有状态和方法
    """

    def __init__(self, config: Config):
        self._config = config
        self._sign_token = ""
        self._run_message = ""
        self._session = create_session()

    @property
    def run_message(self):
        """获取运行消息"""
        return self._run_message

    def add_message(self, msg):
        """添加消息到运行消息"""
        self._run_message += msg + "\n"

    def send_notify(self, title, content):
        """
        通知推送
        使用青龙推送脚本
        """
        import notify

        if self._config.enable_notify:
            # 禁用控制台输出，避免重复打印签到日志
            notify.push_config['CONSOLE'] = False
            # 推送的通知内容大概率不是等宽显示而无法对齐，那就删掉多余空格制表符好了
            content = re.sub(r'[ \t]+', ' ', content)
            notify.send(title, content)

    def generate_sign(self, path, body):
        """生成接口签名"""
        timestamp = str(int(time.time()) - 2)
        token_bytes = self._sign_token.encode("utf-8")
        sign_header = json.loads(json.dumps(SIGN_HEADER_TPL))
        sign_header["timestamp"] = timestamp
        sign_header_str = json.dumps(sign_header, separators=(",", ":"))
        sign_str = path + body + timestamp + sign_header_str
        hmac_hex = hmac.new(token_bytes, sign_str.encode("utf-8"), hashlib.sha256).hexdigest()
        md5_sign = hashlib.md5(hmac_hex.encode("utf-8")).hexdigest()
        return md5_sign, sign_header

    def get_sign_header(self, url, method, body, headers):
        """组装带签名的请求头"""
        header = json.loads(json.dumps(headers))
        parse_url = parse.urlparse(url)
        if method.lower() == "get":
            sign, sign_header = self.generate_sign(parse_url.path, parse_url.query)
        else:
            sign, sign_header = self.generate_sign(parse_url.path, json.dumps(body))
        header["sign"] = sign
        header.update(sign_header)
        return header

    def get_grant_code(self, token):
        """通过 token 获取 grant code"""
        resp = self._session.post(
            GRANT_CODE_URL,
            json={"appCode": APP_CODE, "token": token, "type": 0},
            headers=USER_AGENT,
            timeout=REQUEST_TIMEOUT,
        ).json()
        if resp["status"] != 0:
            raise Exception(f'获取 grant code 失败：{resp["msg"]}')
        return resp["data"]["code"]

    def get_cred(self, grant_code):
        """通过 grant code 获取 cred"""
        resp = self._session.post(
            CRED_CODE_URL,
            json={"code": grant_code, "kind": 1},
            headers=USER_AGENT,
            timeout=REQUEST_TIMEOUT,
        ).json()
        if resp["code"] != 0:
            raise Exception(f'获取 cred 失败：{resp["message"]}')
        self._sign_token = resp["data"]["token"]
        return resp["data"]["cred"]

    def login(self, token):
        """森空岛登录核心逻辑"""
        try:
            parsed_token = json.loads(token)
            token = parsed_token["data"]["content"]
        except Exception:
            pass
        grant = self.get_grant_code(token)
        cred = self.get_cred(grant)
        return cred

    def get_roles(self, cred, app_code):
        """获取绑定的角色列表"""
        header = self.get_sign_header(BINDING_URL, "get", None, USER_AGENT)
        header["cred"] = cred
        resp = self._session.get(
            BINDING_URL,
            headers=header,
            timeout=REQUEST_TIMEOUT
        ).json()
        if resp["code"] != 0:
            raise Exception(f'获取角色失败：{resp["message"]}')
        roles = []
        for app in resp["data"]["list"]:
            if app.get("appCode") == app_code:
                roles.extend(app.get("bindingList", []))
        return roles

    def _parse_checkin_response(self, resp):
        """
        解析签到响应
        返回结果消息
        """
        if resp["code"] != 0:
            error_msg = resp.get("message", "未知错误")
            if "请勿重复签到" in error_msg:
                return "今日已签到，请勿重复签到"
            return f"签到失败：{error_msg}"
        return "ok"

    def _checkin_arknights(self, cred, role, game_config):
        """明日方舟签到"""
        role_name = role.get("nickName", "未知角色")
        channel = role.get("channelName", "未知渠道")
        req_body = {"uid": role.get("uid"), "gameId": role.get("channelMasterId")}
        signed_header = self.get_sign_header(game_config["checkin_url"], "post", req_body, USER_AGENT)
        signed_header["cred"] = cred
        resp = self._session.post(
            game_config["checkin_url"],
            headers=signed_header,
            timeout=REQUEST_TIMEOUT,
            json=req_body
        ).json()
        result = self._parse_checkin_response(resp)
        if result == "ok":
            awards = resp["data"]["awards"]
            award_text = [f'{a["resource"]["name"]}x{a.get("count") or 1}' for a in awards]
            result = f'成功！获得：{"、".join(award_text)}'
        return _build_msg(game_config["name"], role_name, channel, result)

    def _checkin_endfield(self, cred, role, game_config):
        """终末地签到"""
        default_role = role.get("defaultRole") or {}
        role_name = default_role.get("nickname", "未知角色")
        channel = role.get("channelName", "未知渠道")
        role_id = default_role.get("roleId")
        server_id = default_role.get("serverId")
        if not all([role_id, server_id]):
            return _build_msg(game_config["name"], role_name, channel, "缺少角色参数，无法签到")
        req_body = {"uid": role.get("uid"), "gameId": 3, "roleId": role_id, "serverId": server_id}
        signed_header = self.get_sign_header(game_config["checkin_url"], "post", req_body, USER_AGENT)
        signed_header["cred"] = cred
        resp = self._session.post(
            game_config["checkin_url"],
            headers=signed_header,
            timeout=REQUEST_TIMEOUT,
            json=req_body
        ).json()
        result = self._parse_checkin_response(resp)
        if result == "ok":
            award_ids = resp["data"].get("awardIds", [])
            resource_map = resp["data"].get("resourceInfoMap", {})
            if award_ids and resource_map:
                award_text = []
                for award in award_ids:
                    award_id = award.get("id")
                    if award_id and award_id in resource_map:
                        res = resource_map[award_id]
                        award_text.append(f'{res["name"]}x{res.get("count", 1)}')
                result = (f'成功！获得：{"、".join(award_text)}' if award_text else "成功（未识别到奖励信息）")
            else:
                result = "签到成功（无奖励信息）"
        return _build_msg(game_config["name"], role_name, channel, result)

    def __post_init__(self):
        """初始化签到处理器映射"""
        self.CHECKIN_HANDLERS = {
            "arknights": self._checkin_arknights,
            "endfield": self._checkin_endfield,
        }

    def do_daily_checkin(self, cred):
        """执行每日签到"""
        for game_key, game_config in GAME_CONFIG.items():
            try:
                roles = self.get_roles(cred, game_config["app_code"])
                if not roles:
                    continue
                handler = self.CHECKIN_HANDLERS.get(game_key)
                if not handler:
                    continue
                for role in roles:
                    try:
                        msg = handler(cred, role, game_config)
                    except Exception as e:
                        msg = f'[{game_config["name"]}] 角色签到失败：{str(e)}'
                        print(msg)
                    self.add_message(msg)
                    print(msg)
            except Exception as e:
                msg = f'[{game_config["name"]}] 签到失败：{str(e)}'
                self.add_message(msg)
                print(msg)

    def run(self):
        """脚本主逻辑"""
        if not self._config.tokens:
            err_msg = "错误：未配置 SKYLAND_TOKEN 环境变量"
            self.add_message(err_msg)
            print(err_msg)
            self.send_notify("森空岛每日签到", err_msg)
            return
        total_tokens = len(self._config.tokens)
        for idx, token in enumerate(self._config.tokens, 1):
            checkin_msg = f"===== 正在签到 账号[{idx}] ====="
            self.add_message(checkin_msg)
            print(checkin_msg)
            try:
                cred = self.login(token)
                self.do_daily_checkin(cred)
            except Exception as e:
                err_msg = f"[账号{idx}] 签到失败：{str(e)}"
                self.add_message(err_msg)
                print(err_msg)
            complete_msg = f"===== 账号[{idx}] 签到完成 =====\n"
            self.add_message(complete_msg)
            print(complete_msg)
            if idx < total_tokens:
                time.sleep(ACCOUNT_INTERVAL)  # 账号等待间隔
        if self.run_message:
            self.send_notify("森空岛每日签到结果", self.run_message)


def main():
    """脚本主入口"""
    config = Config.from_env()
    checkin = SkylandCheckin(config)
    checkin.__post_init__()  # 手动调用初始化
    checkin.run()


if __name__ == "__main__":
    main()
