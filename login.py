from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
import time
import traceback
import datetime
import hashlib
import re
import requests  # 新增：接口请求依赖

# -------------------------- 核心配置 --------------------------
# 账号获取接口
GET_ACCOUNTS_API = "https://appui.ittc.top/get_accounts.php?token=dsjoqowo2922ek1s"
# 数据提交接口（纯URL，Token放JSON体里）
SUBMIT_API = "https://appui.ittc.top/submit_success.php"
# 接口通用Token（关键：放到JSON请求体里）
API_TOKEN = "dsjoqowo2922ek1s"
# 登录地址
LOGIN_URL = "https://ai.cqvie.edu.cn"
# ChromeDriver路径
CHROME_DRIVER_PATH = r"C:\Users\Administrator\Desktop\cqvie\chromedriver.exe"
# 需要提取的Cookie字段
TARGET_COOKIE_FIELDS = ['username', 'rememberMe', 'mysec_sign_javascript',
                        'mysec_sign_cookie', 'Admin-Token', 'JSESSIONID']

# 真实Chrome UA模板（仅替换版本号部分）
UA_TEMPLATE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{VERSION} Safari/537.36"


def hash_account_to_ua(username):
    """
    基于账号哈希生成唯一且固定的专属UA（确定性哈希）
    :param username: 账号名
    :return: 专属绑定的真实格式UA
    """
    # 1. 对账号做MD5哈希（确保确定性+唯一性）
    md5_hash = hashlib.md5(username.encode('utf-8')).hexdigest()
    # 2. 提取哈希前6位，转为数字（模拟Chrome主版本号，范围100-200）
    hash_num = int(md5_hash[:6], 16) % 100 + 100  # 100-199之间的整数
    # 3. 构造Chrome版本号（主版本.0.0.0）
    chrome_version = f"{hash_num}.0.0.0"
    # 4. 生成专属UA
    bound_ua = UA_TEMPLATE.format(VERSION=chrome_version)
    # 验证：确保UA格式合法
    assert re.match(r'^Mozilla/5.0 .* Chrome/\d+\.0\.0\.0 Safari/537\.36$', bound_ua), "UA格式异常"
    return bound_ua


def get_api_accounts():
    """从接口读取账号 + 为每个账号生成哈希绑定的专属UA（适配实际接口返回格式）"""
    accounts = []
    current_date = datetime.date.today()
    # 支持的常见日期格式
    SUPPORTED_DATE_FORMATS = [
        "%Y-%m-%d",  # 2026-01-25
        "%Y/%m/%d",  # 2026/1/25
        "%Y.%m.%d",  # 2026.01.25
        "%Y年%m月%d日",  # 2026年1月25日
        "%Y-%d-%m"  # 兼容反向格式
    ]

    try:
        # 请求账号接口（添加超时和UA伪装）
        print(f"🔍 正在请求账号接口：{GET_ACCOUNTS_API}")
        response = requests.get(
            GET_ACCOUNTS_API,
            timeout=30,  # 超时时间30秒
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json"
            }
        )
        response.raise_for_status()  # 非200状态码抛出异常
        api_data = response.json()
        print(f"✅ 接口返回原始数据：{api_data}")

        # ========== 核心适配：解析接口返回的字典结构 ==========
        # 1. 校验接口返回的基础结构
        if not isinstance(api_data, dict) or "data" not in api_data:
            print(f"❌ 接口返回数据结构错误，缺少data字段")
            return []
        
        # 2. 提取有效账号列表（优先取valid，这是接口返回的未过期账号）
        valid_accounts = api_data["data"].get("valid", [])
        if not isinstance(valid_accounts, list):
            print(f"❌ 接口data.valid不是列表，实际：{type(valid_accounts)}")
            return []
        
        print(f"✅ 从接口提取到{len(valid_accounts)}个有效未过期账号")

        # 3. 遍历有效账号列表
        for row_num, item in enumerate(valid_accounts, start=1):
            # -------- 适配接口实际字段名 --------
            username = item.get('account')  # 接口返回的账号字段是account
            password = item.get('password')  # 接口返回的密码字段是password
            date_data = item.get('vip')  # 接口返回的有效期字段是vip

            # 过滤不完整数据
            if not (username and password and date_data):
                print(f"⚠️ 第{row_num}条数据缺失（账号/密码/日期），跳过")
                continue

            # 处理日期对象/带时间的字符串
            if isinstance(date_data, (datetime.datetime, datetime.date)):
                date_str = date_data.strftime("%Y-%m-%d")
            else:
                date_str = str(date_data).strip().split()[0]  # 去掉时间部分

            # 尝试多种格式解析日期
            account_date = None
            for fmt in SUPPORTED_DATE_FORMATS:
                try:
                    account_date = datetime.datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue

            if not account_date:
                print(f"⚠️ 第{row_num}条日期格式不支持（当前：{date_str}），跳过")
                continue

            # 核心：为账号生成哈希绑定的专属UA（固定且唯一）
            bound_ua = hash_account_to_ua(str(username).strip())
            print(f"✅ 第{row_num}条账号{username}哈希绑定UA：{bound_ua}")

            accounts.append({
                "row_num": row_num,
                "username": str(username).strip(),
                "password": str(password).strip(),
                "account_date": account_date,
                "bound_ua": bound_ua  # 哈希绑定的专属UA
            })

        print(f"\n✅ 成功从接口获取账号，共获取{len(accounts)}个有效账号（均已哈希绑定专属UA）")
        return accounts

    except requests.exceptions.Timeout:
        print(f"❌ 请求账号接口超时（30秒）")
        return []
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接到账号接口，请检查网络或接口地址")
        return []
    except requests.exceptions.HTTPError as e:
        print(f"❌ 账号接口返回错误状态码：{e.response.status_code}，响应：{e.response.text[:200]}")
        return []
    except ValueError:
        print(f"❌ 接口返回数据不是合法JSON格式")
        return []
    except Exception as e:
        print(f"❌ 处理接口数据失败：{str(e)}")
        traceback.print_exc()
        return []


def login_and_set_hash_ua(username, password, row_num, bound_ua):
    """单个账号登录（强制修改浏览器UA）+ 提取Cookie + 提交到接口（适配PHP源码）"""
    print(f"\n{'=' * 80}")
    print(f"开始处理第{row_num}条账号：{username}")
    print(f"哈希绑定专属UA：{bound_ua}")
    print(f"{'=' * 80}")

    # Chrome配置（强制修改UA + 隐藏自动化特征）
    options = webdriver.ChromeOptions()
    # 1. 无头模式（可选：注释掉可看浏览器操作）
    options.add_argument("--headless=new")
    # 2. 窗口大小
    options.add_argument("--window-size=1920,1080")
    # 3. 隐藏自动化特征
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option('useAutomationExtension', False)
    # 4. 核心：强制设置浏览器UA为哈希绑定值
    options.add_argument(f'--user-agent={bound_ua}')
    # 5. 禁用自动化控制特征（防止UA被篡改）
    options.add_argument("--disable-blink-features=AutomationControlled")
    # 6. 其他优化
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")

    driver = None
    try:
        # 初始化驱动
        print("🔍 初始化Chrome浏览器（强制修改UA为哈希绑定值）...")
        service = Service(executable_path=CHROME_DRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=options)

        # 强制覆盖JS层面的UA（双重保障）
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': f'''
                Object.defineProperty(navigator, "webdriver", {{get: () => undefined}});
                Object.defineProperty(navigator, "userAgent", {{get: () => "{bound_ua}"}});
                Object.defineProperty(navigator, "languages", {{get: () => ["zh-CN", "zh"]}});
                Object.defineProperty(navigator, "plugins", {{get: () => [{{}}]}});
            '''
        })

        driver.implicitly_wait(10)
        wait = WebDriverWait(driver, 20)

        # 访问登录页
        driver.get(LOGIN_URL)
        print(f"✅ 已打开登录页：{LOGIN_URL}")

        # 验证UA修改成功
        js_ua = driver.execute_script("return navigator.userAgent;")
        assert js_ua == bound_ua, f"JS UA修改失败！预期：{bound_ua}，实际：{js_ua}"
        print(f"✅ JS层面UA修改验证通过：{js_ua}")

        # 输入账号密码
        username_input = wait.until(
            EC.element_to_be_clickable((By.XPATH, '//input[@class="el-input__inner" and @type="text"]')))
        username_input.clear()
        username_input.send_keys(username)

        password_input = wait.until(
            EC.element_to_be_clickable((By.XPATH, '//input[@class="el-input__inner" and @type="password"]')))
        password_input.clear()
        password_input.send_keys(password)
        print("✅ 账号密码输入完成")

        # 点击登录
        login_button = wait.until(
            EC.element_to_be_clickable((By.XPATH, '//button[@class="el-button el-button--primary" and span="登录"]')))
        login_button.click()
        print("✅ 已点击登录按钮")

        time.sleep(3)
        if "login" not in driver.current_url.lower():
            print("🎉 登录成功！正在查找「顶岗实习系统」...")

            # 等待并点击顶岗实习系统
            internship_system = wait.until(
                EC.element_to_be_clickable((By.XPATH, '//span[@class="ft-size14 ml15" and text()="顶岗实习系统"]'))
            )
            internship_system.click()
            print("✅ 已点击「顶岗实习系统」")

            time.sleep(6)
            # 切换到新标签页
            if len(driver.window_handles) >= 2:
                driver.switch_to.window(driver.window_handles[-1])
                print(f"✅ 切换到新标签页：{driver.current_url}")

            time.sleep(5)
            all_cookies = driver.get_cookies()

            # 筛选目标Cookie
            target_cookie_str = ""
            for field in TARGET_COOKIE_FIELDS:
                cookie_value = next((c['value'] for c in all_cookies if c['name'] == field), "")
                target_cookie_str += f"{field}={cookie_value}; "
            target_cookie_str = target_cookie_str.strip().rstrip(';')

            print("\n📌 目标Cookie：")
            print("-" * 60)
            print(target_cookie_str)
            print("-" * 60)

            # 保存Cookie到本地文件
            cookie_filename = f"cookie_{username}.txt"
            with open(cookie_filename, "w", encoding="utf-8") as f:
                f.write(f"专属UA：{bound_ua}\n")
                f.write(f"Cookie：{target_cookie_str}")
            print(f"✅ Cookie+UA已保存到 {cookie_filename}")

            # 追加到汇总文件
            with open("所有账号Cookie汇总.txt", "a", encoding="utf-8") as f:
                f.write(f"账号：{username}\n")
                f.write(f"专属UA：{bound_ua}\n")
                f.write(f"Cookie：{target_cookie_str}\n")
                f.write("-" * 80 + "\n")
            print(f"✅ 已追加到 所有账号Cookie汇总.txt")

            # -------------------------- 完全适配PHP接口：提交数据 --------------------------
            print(f"\n🔍 正在提交数据到PHP接口：{SUBMIT_API}")
            # 核心：按照PHP接口要求构造JSON请求体（包含token字段）
            submit_data = {
                "token": API_TOKEN,        # PHP接口要求token在JSON体里（关键修复）
                "account": username,       # 账号
                "agent": bound_ua,         # 专属UA（agent）
                "cookie_str": target_cookie_str  # Cookie字符串
            }
            
            # 构建请求头（仅保留必要的JSON格式声明）
            submit_headers = {
                "User-Agent": bound_ua,
                "Content-Type": "application/json"  # 强制JSON格式（PHP用php://input接收）
            }

            # 提交请求（严格适配PHP接口）
            try:
                submit_response = requests.post(
                    SUBMIT_API,
                    json=submit_data,  # JSON格式提交（必须）
                    headers=submit_headers,
                    timeout=30,
                    verify=False  # 忽略SSL证书（如有需要）
                )
                
                # 解析响应
                submit_response.raise_for_status()
                submit_result = submit_response.json()
                print(f"✅ PHP接口返回结果：{submit_result}")
                
                # 校验提交结果（匹配PHP的返回码）
                if submit_result.get("code") == 200:
                    print(f"🎉 账号{username}数据提交成功！已写入数据库")
                else:
                    error_msg = submit_result.get("msg", "未知错误")
                    print(f"❌ 账号{username}提交失败：{error_msg}")
                    with open("登录失败账号.txt", "a", encoding="utf-8") as f:
                        f.write(f"第{row_num}条 | 账号：{username} | 专属UA：{bound_ua} | 原因：{error_msg}\n")

            except requests.exceptions.RequestException as e:
                error_note = f"接口请求失败：{str(e)}"
                print(f"❌ {error_note}")
                with open("登录失败账号.txt", "a", encoding="utf-8") as f:
                    f.write(f"第{row_num}条 | 账号：{username} | 专属UA：{bound_ua} | 原因：{error_note}\n")
            except ValueError:
                error_note = f"接口返回非JSON格式：{submit_response.text[:200]}"
                print(f"❌ {error_note}")
                with open("登录失败账号.txt", "a", encoding="utf-8") as f:
                    f.write(f"第{row_num}条 | 账号：{username} | 专属UA：{bound_ua} | 原因：{error_note}\n")

        else:
            print(f"❌ 账号{username}登录失败（URL仍包含login）")
            with open("登录失败账号.txt", "a", encoding="utf-8") as f:
                f.write(f"第{row_num}条 | 账号：{username} | 专属UA：{bound_ua} | 原因：登录失败\n")

    except AssertionError as ae:
        print(f"\n❌ UA修改验证失败：{ae}")
        with open("登录失败账号.txt", "a", encoding="utf-8") as f:
            f.write(f"第{row_num}条 | 账号：{username} | 专属UA：{bound_ua} | 原因：{ae}\n")
    except Exception as e:
        print(f"\n❌ 账号{username}处理出错：{str(e)}")
        traceback.print_exc()
        if driver:
            print(f"🔍 报错URL：{driver.current_url}")
        with open("登录失败账号.txt", "a", encoding="utf-8") as f:
            f.write(f"第{row_num}条 | 账号：{username} | 专属UA：{bound_ua} | 原因：{str(e)}\n")
    finally:
        if driver:
            driver.quit()
            print(f"🔚 账号{username}浏览器已关闭")


def main():
    current_date = datetime.date.today()
    print(f"📅 当前日期：{current_date}")
    print(f"🔑 核心逻辑：账号→MD5哈希→专属UA→强制修改浏览器UA→登录→适配PHP接口提交\n")

    # 1. 从接口读取账号并生成哈希UA
    accounts = get_api_accounts()
    if not accounts:
        print("❌ 无有效账号，程序退出")
        return

    # 2. 初始化汇总文件
    with open("所有账号Cookie汇总.txt", "w", encoding="utf-8") as f:
        f.write(f"账号-专属UA-Cookie汇总（生成时间：{current_date}）\n")
        f.write("=" * 80 + "\n")
    with open("登录失败账号.txt", "w", encoding="utf-8") as f:
        f.write(f"登录失败账号汇总（生成时间：{current_date}）\n")
        f.write("=" * 80 + "\n")

    # 3. 批量处理账号（接口返回的已是未过期账号）
    for account in accounts:
        login_and_set_hash_ua(
            username=account["username"],
            password=account["password"],
            row_num=account["row_num"],
            bound_ua=account["bound_ua"]
        )
        time.sleep(2)  # 间隔防风控

    print(f"\n🎉 批量处理完成！")
    print(f"✅ 结果文件：所有账号Cookie汇总.txt、登录失败账号.txt、cookie_<账号>.txt")
    print(f"✅ 成功提交的账号数据已写入PHP接口的数据库（cookie表）")


if __name__ == "__main__":
    # 安装依赖（首次运行需执行）
    # pip install selenium requests
    main()