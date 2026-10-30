"""服务层异常：接口层统一转换成 {"ok": false, "error": {"type", "message"}}。"""

from __future__ import annotations


class ServiceError(Exception):
    status_code = 400
    error_type = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(ServiceError):
    status_code = 404
    error_type = "not_found"


class Forbidden(ServiceError):
    status_code = 403
    error_type = "forbidden"


class Unauthorized(ServiceError):
    status_code = 401
    error_type = "unauthorized"


class Conflict(ServiceError):
    status_code = 409
    error_type = "conflict"
