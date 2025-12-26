import requests
import json
import datetime
import os
import warnings
import time
from datetime import timedelta
import urllib.parse

# 忽略SSL证书警告
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

# 核心配置
SIGN_QUERY_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/signrecord/list?pageNum=1&pageSize=50&internshipPlanSemester=12"
SIGN_SUBMIT_URL = "https://dgsx.cqvie.edu.cn/prod-api/internship_pending/signrecord"
START_DATE_STR = "2025-11-17"
END_DATE_STR = "2026-01-25"
START_DATE = datetime.datetime.strptime(START_DATE_STR, "%Y-%m-%d")
END_DATE = datetime.datetime.strptime(END_DATE_STR, "%Y-%m-%d")

# 百度API配置
BAIDU_AK = "KkMElpdMI5UYxRaRGKQfOyraNDnNWqdD"
BAIDU_API_TEMPLATE = f"https://api.map.baidu.com/geocoding/v3/?ak={BAIDU_AK}&address={{ENCODED_ADDR}}&output=json"

# 全局Session
session = requests.Session()
session.verify = False
session.timeout = 20
session.max_redirects = 5

# 法定节假日
LEGAL_HOLIDAYS = []

# 默认User-Agent（用户不输入时使用）
DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'


# ---------------------- 工具函数 ----------------------
def get_timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_sign_datetime(date_str):
    return f"{date_str}T09:00:00.999Z"


def parse_cookies(cookie_str):
    cookies = {}
    items = cookie_str.strip(';').split(';')
    for item in items:
        if '=' in item:
            k, *v_parts = item.split('=')
            k = k.strip()
            v = '='.join(v_parts).strip()
            cookies[k] = v
    return cookies


def extract_validation_cookie(html_content):
    try:
        cookie_key = "'cookie' : " if "'cookie' : " in html_content else '"cookie" : '
        start_idx = html_content.find(cookie_key) + len(cookie_key)
        quote_char = html_content[start_idx]
        end_idx = html_content.find(quote_char, start_idx + 1)
        return {'mysec_sign_cookie': html_content[start_idx + 1:end_idx]}
    except:
        return None


def is_valid_sign_date(date_str):
    try:
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        now = datetime.datetime.now()
        if date > now or not (START_DATE <= date <= END_DATE) or date.weekday() in [5, 6] or date_str in LEGAL_HOLIDAYS:
            return False
        return True
    except:
        return False


def get_sign_base_info(cookie_str, user_agent):
    temp_session = requests.Session()
    temp_session.verify = False
    temp_session.cookies.update(parse_cookies(cookie_str))
    admin_token = parse_cookies(cookie_str).get('Admin-Token', '')
    if not admin_token:
        return None, None, None

    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Authorization': f'Bearer {admin_token}',
        'Host': 'dgsx.cqvie.edu.cn',
        'Referer': 'https://dgsx.cqvie.edu.cn/internship_pending/signrecord',
        'User-Agent': user_agent  # 使用传入的UA
    }

    try:
        res = temp_session.get(SIGN_QUERY_URL, headers=headers, allow_redirects=False, timeout=20)
        if res.status_code != 200:
            return None, None, None
        res_json = res.json()
        if res_json.get('code') != 200 or not res_json.get('rows'):
            return None, None, None

        valid_records = [r for r in res_json['rows'] if r.get('createTime')]
        if valid_records:
            valid_records_sorted = sorted(valid_records, key=lambda x: datetime.datetime.strptime(x['createTime'], "%Y-%m-%d %H:%M:%S"), reverse=True)
            first_sign = valid_records_sorted[0]
        else:
            first_sign = res_json['rows'][0]

        plan_id = first_sign.get('signInternshipPlanId')
        student_id = first_sign.get('signStudentId')
        latitude = first_sign.get('latitude')
        longitude = first_sign.get('longitude')
        address = first_sign.get('signAddress')
        if all([plan_id, student_id, latitude, longitude, address]):
            return plan_id, student_id, (latitude, longitude, address)
        return None, None, None
    except:
        return None, None, None


def get_lng_lat_from_address(input_address, user_agent):
    try:
        encoded_addr = urllib.parse.quote(input_address)
        api_url = BAIDU_API_TEMPLATE.format(ENCODED_ADDR=encoded_addr)
        headers = {'User-Agent': user_agent}  # 使用传入的UA
        res = requests.get(api_url, headers=headers, timeout=15, verify=False, allow_redirects=False)

        if res.status_code != 200:
            return None, None, f"HTTP{res.status_code}"
        res_json = res.json()
        if res_json.get('status') != 0:
            return None, None, f"status{res_json.get('status')}"

        result = res_json.get('result', {})
        location = result.get('location', {})
        lat = location.get('lat')
        lng = location.get('lng')
        if lat is None or lng is None:
            return None, None, "无经纬度"
        formatted_addr = result.get('formatted_address', input_address)
        return str(lat), str(lng), formatted_addr
    except Exception as e:
        return None, None, str(e)


def get_all_valid_dates():
    valid_dates = []
    now = datetime.datetime.now()
    current = START_DATE
    while current <= min(END_DATE, now):
        date_str = current.strftime("%Y-%m-%d")
        if is_valid_sign_date(date_str):
            valid_dates.append(date_str)
        current += timedelta(days=1)
    return valid_dates


def query_signed_dates(cookie_str, plan_id, user_agent):
    temp_session = requests.Session()
    temp_session.verify = False
    temp_session.cookies.update(parse_cookies(cookie_str))
    admin_token = parse_cookies(cookie_str).get('Admin-Token', '')
    headers = {
        'Authorization': f'Bearer {admin_token}',
        'Referer': 'https://dgsx.cqvie.edu.cn/internship_pending/signrecord',
        'User-Agent': user_agent  # 使用传入的UA
    }
    try:
        res = temp_session.get(SIGN_QUERY_URL, headers=headers, allow_redirects=False, timeout=20)
        if res.status_code != 200:
            return []
        res_json = res.json()
        if res_json.get('code') != 200 or not res_json.get('rows'):
            return []
        return [item.get('signDate') for item in res_json['rows'] if item.get('signDate')]
    except:
        return []


def init_session(cookie_str, user_agent):
    session.cookies.clear()
    session.cookies.update(parse_cookies(cookie_str))
    try:
        headers = {'User-Agent': user_agent}  # 使用传入的UA
        response = session.get('https://dgsx.cqvie.edu.cn/internship_pending/signrecord', headers=headers, allow_redirects=True, timeout=20)
        return response.status_code == 200
    except:
        return False


def send_sign_request(request_data, user_agent):
    cookie_dict = session.cookies.get_dict()
    admin_token = cookie_dict.get('Admin-Token', '')
    headers = {
        'Authorization': f'Bearer {admin_token}',
        'Content-Type': 'application/json;charset=UTF-8',
        'Referer': 'https://dgsx.cqvie.edu.cn/internship-student/sign-in',
        'User-Agent': user_agent  # 使用传入的UA
    }
    response = session.post(SIGN_SUBMIT_URL, headers=headers, data=json.dumps(request_data), allow_redirects=True)
    if response.status_code == 200 and 'text/html' in response.headers.get('Content-Type', ''):
        validation_cookies = extract_validation_cookie(response.text)
        if validation_cookies:
            session.cookies.update(validation_cookies)
            response = session.post(SIGN_SUBMIT_URL, headers=headers, data=json.dumps(request_data), allow_redirects=True)
    return response


def submit_sign(date_str, plan_id, student_id, location, is_today=False, user_agent=None):
    sign_type = "0" if is_today else "1"
    latitude, longitude, address = location
    request_data = {
        "signDate": format_sign_datetime(date_str),
        "signAddress": address,
        "latitude": latitude,
        "longitude": longitude,
        "signInternshipPlanId": plan_id,
        "signType": sign_type,
        "signStudentId": student_id
    }
    try:
        response = send_sign_request(request_data, user_agent)
        if response.status_code == 200:
            try:
                res = response.json()
                return res.get('code') in [0, 200], res.get('msg', '成功')
            except:
                return False, "Cookie过期"
        return False, f"状态码{response.status_code}"
    except:
        return False, "请求异常"


# ---------------------- 主逻辑 ----------------------
def sign():
    result = {"timestamp": get_timestamp(), "status": "fail", "msg": "未知错误"}
    # 1. 输入Cookie
    try:
        cookie_str = input("请输入COOKIE_STR：> ").strip()
        if not cookie_str:
            result["msg"] = "Cookie为空"
            print(json.dumps(result, ensure_ascii=False))
            return
    except:
        result["msg"] = "读取Cookie失败"
        print(json.dumps(result, ensure_ascii=False))
        return

    # 2. 输入地址
    try:
        input_address = input("请输入签到地址：> ").strip()
        if not input_address:
            result["msg"] = "地址为空"
            print(json.dumps(result, ensure_ascii=False))
            return
    except:
        result["msg"] = "读取地址失败"
        print(json.dumps(result, ensure_ascii=False))
        return

    # 3. 输入User-Agent（新增）
    try:
        user_agent_input = input(f"请输入User-Agent（回车使用默认> ").strip()
        user_agent = user_agent_input if user_agent_input else DEFAULT_USER_AGENT
    except:
        result["msg"] = "读取User-Agent失败"
        print(json.dumps(result, ensure_ascii=False))
        return

    # 4. 解析地址
    lat, lng, addr_result = get_lng_lat_from_address(input_address, user_agent)
    if not lat or not lng:
        result["msg"] = f"地址解析失败：{addr_result}"
        print(json.dumps(result, ensure_ascii=False))
        return
    location = (lat, lng, addr_result)

    # 5. 提取基础信息
    plan_id, student_id, _ = get_sign_base_info(cookie_str, user_agent)
    if not plan_id or not student_id:
        result["msg"] = "提取基础信息失败"
        print(json.dumps(result, ensure_ascii=False))
        return

    # 6. 初始化会话
    if not init_session(cookie_str, user_agent):
        result["msg"] = "会话初始化失败"
        print(json.dumps(result, ensure_ascii=False))
        return

    # 7. 获取待签到日期
    all_valid_dates = get_all_valid_dates()
    signed_dates = query_signed_dates(cookie_str, plan_id, user_agent)
    un_signed_dates = [d for d in all_valid_dates if d not in signed_dates]
    if not un_signed_dates:
        result["status"] = "success"
        result["msg"] = "所有日期已签到"
        print(json.dumps(result, ensure_ascii=False))
        return

    # 8. 执行签到
    success_count = 0
    fail_count = 0
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # 当日签到
    if today_str in un_signed_dates:
        success, _ = submit_sign(today_str, plan_id, student_id, location, is_today=True, user_agent=user_agent)
        success_count += 1 if success else 0
        fail_count += 1 if not success else 0
        un_signed_dates.remove(today_str)
        time.sleep(5)

    # 历史补签
    for date_str in sorted(un_signed_dates):
        success, _ = submit_sign(date_str, plan_id, student_id, location, user_agent=user_agent)
        success_count += 1 if success else 0
        fail_count += 1 if not success else 0
        time.sleep(5)

    # 9. 生成结果
    total = len(un_signed_dates) + (1 if today_str in all_valid_dates and today_str not in signed_dates else 0)
    if success_count > 0:
        result["status"] = "success"
        result["msg"] = f"处理{total}天，成功{success_count}天，失败{fail_count}天"
    else:
        result["msg"] = f"处理{total}天，全部失败"
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    if os.name == 'nt':
        os.system('chcp 65001 > nul 2>&1')
    try:
        sign()
    except KeyboardInterrupt:
        print(json.dumps({"timestamp": get_timestamp(), "status": "fail", "msg": "用户中断操作"}, ensure_ascii=False))