"""
用户认证路由
"""
from flask import Blueprint, request, jsonify, session
from app.auth.authorization import (
    DEFAULT_ADMIN_PAGE,
    DEFAULT_USER_PAGE,
    safe_return_target,
)
from app.auth.csrf import ensure_csrf_token
from app.auth.rbac import (
    PERMISSION_ADMIN_ACCESS,
    get_user_permissions,
    get_user_role_keys,
    primary_role_key,
)
from app.auth.session_guard import get_current_session_user
from app.request_context import bind_request_log_context
from observability.logging_runtime import log_event
import bcrypt
import logging


LOGGER = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/api')
LAST_LOGIN_RECORD_FAILED_WARNING = 'last_login_record_failed'

# 获取注册值,检查注册值
@auth_bp.route('/register', methods=['POST'])
def handle_register():
    """
    处理用户注册请求。
    接收前端通过HTTPS发送的明文密码，在后端使用bcrypt进行哈希。
    """
    data = request.json
    username = data.get('username')
    plain_password = data.get('password')  # 接收明文密码（HTTPS保护传输）

    if not username or not plain_password:
        return jsonify({'success': False, 'error': '缺少用户名或密码'}), 400

    # 基本的用户名和密码格式验证
    if len(username) < 3:
        return jsonify({'success': False, 'error': '用户名至少需要3个字符'}), 400
    
    # 密码长度验证（建议至少6位，可根据需求调整）
    if len(plain_password) < 6:
        return jsonify({'success': False, 'error': '密码至少需要6个字符'}), 400
    
    # 密码强度验证
    if not any(c.isdigit() for c in plain_password):
        return jsonify({'success': False, 'error': '密码必须包含至少一个数字'}), 400

    from app.auth.service import register_user

    success, message = register_user(username, plain_password)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': message}), 400 # 用户名已存在等是客户端错误

# 检查登录值
@auth_bp.route('/login', methods=['POST'])
def handle_login():
    """
    处理用户登录请求。
    接收前端通过HTTPS发送的明文密码，使用bcrypt进行验证。
    """
    from app.auth.service import find_user, record_successful_login


    data = request.json
    username = data.get('username')
    plain_password = data.get('password')  # 接收明文密码（HTTPS保护传输）
    requested_next = data.get('next')

    if not username or not plain_password:
        return jsonify({'success': False, 'error': '缺少用户名或密码'}), 400

    user_data = find_user(username)

    if not user_data:
        return jsonify({'success': False, 'error': '用户名不存在'}), 401 # 401 Unauthorized

    if not user_data['is_active']:
        log_event(
            LOGGER,
            "security.login.disabled_account",
        )
        return jsonify({'success': False, 'error': '账号已被禁用'}), 403

    # 使用 bcrypt 验证密码
    # bcrypt.checkpw() 会自动从存储的哈希中提取盐值进行验证
    stored_hashed_password = user_data["password_hash"].encode('utf-8')
    if bcrypt.checkpw(plain_password.encode('utf-8'), stored_hashed_password):
        last_login_recorded = record_successful_login(user_data['id'])

        # 角色和权限以 user_roles/role_permissions 为准，Session 只保存身份与撤销版本。
        role_keys = get_user_role_keys(user_data['id'])
        permissions = get_user_permissions(user_data['id'])
        role = primary_role_key(role_keys)

        #  核心修改：在 Session 中存储用户信息 
        session.clear() # 先清除旧的会话数据
        session['user_id'] = user_data['id']
        session['username'] = user_data['username']
        session['auth_version'] = int(user_data.get('auth_version') or 1)
        bind_request_log_context(user_id=user_data["id"])
        # 登录成功由后端自己记录，不依赖前端上报的结果。
        log_event(LOGGER, "analytics.auth.login_success")
        csrf_token = ensure_csrf_token()
        # Session 会自动通过浏览器 cookie 维护状态，不再需要文件

        redirect_to = safe_return_target(requested_next)
        if redirect_to is None:
            redirect_to = (
                DEFAULT_ADMIN_PAGE
                if PERMISSION_ADMIN_ACCESS in permissions
                else DEFAULT_USER_PAGE
            )

        response_payload = {
            'success': True,
            'username': username,
            'role': role,
            'permissions': sorted(permissions),
            'csrf_token': csrf_token,
            'redirect_to': redirect_to,
        }
        if not last_login_recorded:
            response_payload['warning_code'] = LAST_LOGIN_RECORD_FAILED_WARNING
        return jsonify(response_payload)
    else:
        return jsonify({'success': False, 'error': '密码错误'}), 401 # 401 Unauthorized

# 登出
@auth_bp.route('/logout', methods=['POST'])
def handle_logout():
    """清空当前浏览器对应的后端登录会话。"""
    #  核心修改：清除会话 
    session.clear()

    return jsonify({'success': True})

#  检查认证状态 API 端点 
@auth_bp.route('/check_auth', methods=['GET'])
def check_auth():
    """从主库重新确认当前后端记录的登录状态。"""

    current_user = get_current_session_user()
    if current_user:
        username = current_user['username']
        permissions = get_user_permissions(current_user['id'])
        return jsonify({
            'isLoggedIn': True,
            'username': username,
            'role': primary_role_key(get_user_role_keys(current_user['id'])),
            'permissions': sorted(permissions),
            'csrf_token': ensure_csrf_token(),
        })
    else:
        return jsonify({'isLoggedIn': False})
