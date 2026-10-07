'''
app.auth.service - 用户认证服务

- 用户查询
- 密码哈希
- 注册用户

'''
from app.db import get_read_connection, get_write_connection, record_database_failure
from app.auth.rbac import ROLE_USER, replace_user_roles
import mysql.connector
import logging
import bcrypt
from mysql.connector import errorcode       
from observability.logging_runtime import log_event


MANAGED_PASSWORD_MIN_CHARS = 15
MANAGED_PASSWORD_MAX_CHARS = 64
BCRYPT_MAX_PASSWORD_BYTES = 72


LOGGER = logging.getLogger(__name__)


def _last_login_failure_reason(exc: Exception) -> str:
    """把登录时间写入失败映射为稳定、可查询的原因码。"""
    if isinstance(exc, TimeoutError):
        return "query_timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return "connection_unavailable"
    return "unexpected_error"


# 查找用户
def find_user(username):
    """按用户名从主库读取登录所需的用户、角色和启用状态。"""
    try:
        # with提供一个临时变量，储存这个函数
        with get_read_connection(consistency="strong") as conn:
            # 使用 dictionary=True 使 cursor 返回字典而不是元组，方便按列名访问
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, username, password_hash, role, is_active, auth_version
                FROM users
                WHERE username = %s
                """,
                (username,),
            )
            user_row = cursor.fetchone()
            # cursor.close() # 'with' 语句会自动关闭游标和连接
            if user_row:
                # 返回包含登录、角色和启用状态的用户字典
                return user_row # user_row 已经是字典了
            return None
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="auth_lookup_query")
        return None
    except Exception:
        return None


def find_user_by_id(user_id):
    """按 ID 从主库读取当前角色、启用状态和认证版本；未找到返回 None。"""
    with get_read_connection(consistency="strong") as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, username, role, is_active, auth_version
            FROM users
            WHERE id = %s
            """,
            (user_id,),
        )
        return cursor.fetchone()


def record_successful_login(user_id: int) -> bool:
    """在主库记录一次密码验证成功的登录时间，失败时返回 False 而不中断登录。"""
    try:
        with get_write_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE users
                SET last_login_at = UTC_TIMESTAMP()
                WHERE id = %s
                """,
                (user_id,),
            )
            conn.commit()
        return True
    except mysql.connector.Error as exc:
        record_database_failure(exc, operation="last_login_write")
        return False
    except Exception as exc:
        log_event(
            LOGGER,
            "auth.login.last_login_update_failed",
            details={"reason_code": _last_login_failure_reason(exc)},
            exc_info=True,
        )
        return False


# 哈希密码
def hash_password(password):
    """使用 bcrypt 为明文密码生成不可逆哈希。"""
    hashed_password = bcrypt.hashpw(
        password.encode('utf-8'), 
        bcrypt.gensalt(rounds=12)).decode('utf-8')
    
    return hashed_password


def verify_password(plain_password: str, stored_hash: str | bytes) -> bool:
    """常量时间校验 bcrypt 密码；损坏哈希或异常输入统一视为不匹配。"""
    if not isinstance(plain_password, str) or not plain_password:
        return False
    try:
        encoded_hash = (
            stored_hash
            if isinstance(stored_hash, bytes)
            else str(stored_hash).encode("utf-8")
        )
        return bcrypt.checkpw(plain_password.encode("utf-8"), encoded_hash)
    except (TypeError, ValueError):
        return False


def managed_password_error(password: object) -> str | None:
    """校验管理员受控改密口令长度，并避免 bcrypt 72 字节静默截断。"""
    if not isinstance(password, str):
        return "新密码必须是字符串"
    if len(password) < MANAGED_PASSWORD_MIN_CHARS:
        return f"新密码至少需要 {MANAGED_PASSWORD_MIN_CHARS} 个字符"
    if len(password) > MANAGED_PASSWORD_MAX_CHARS:
        return f"新密码不能超过 {MANAGED_PASSWORD_MAX_CHARS} 个字符"
    if len(password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        return "新密码的 UTF-8 编码不能超过 72 字节"
    return None

# 注册用户
def register_user(username, plain_password):
    """
    注册新用户，使用 bcrypt 对明文密码进行哈希。
    
    Args:
        username: 用户名
        plain_password: 前端发送的明文密码（通过HTTPS保护）
    
    Returns:
        (success: bool, message: str)
    """
    if find_user(username): # 首先检查用户是否存在
        return False, "用户名已被注册。"

    try:
        with get_write_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            # 使用 bcrypt 对明文密码进行哈希（包含自动生成的盐值）
            hashed_password = hash_password(plain_password)
            cursor.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                           (username, hashed_password))
            replace_user_roles(cursor, int(cursor.lastrowid), (ROLE_USER,))
            conn.commit()
        return True, "注册成功！"
    except mysql.connector.Error as e: # <-- 修改异常类型
        # MySQL 的 IntegrityError 对于 UNIQUE 约束冲突通常是 ER_DUP_ENTRY (errno 1062)
        # 这里对应的是mysql报错文档
        if e.errno == errorcode.ER_DUP_ENTRY:
            return False, "用户名已被注册。"
        record_database_failure(e, operation="auth_register_write")
        return False, "注册过程中发生服务器错误。"
    except Exception:
        return False, "注册过程中发生服务器错误。"
