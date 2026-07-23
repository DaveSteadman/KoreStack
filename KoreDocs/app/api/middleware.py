from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if (
            request.url.path.startswith('/static/')
            or request.url.path.startswith('/ui-elements/assets/')
            or request.url.path.startswith('/static/commonui/')
        ):
            response.headers['cache-control'] = 'no-store'
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_token: str | None):
        super().__init__(app)
        self._api_token = api_token

    async def dispatch(self, request: Request, call_next):
        if not self._api_token:
            return await call_next(request)
        path = request.url.path
        protected = (
            path.startswith('/mcp')
            or (path.startswith('/api/') and request.method != 'OPTIONS')
        )
        if not protected:
            return await call_next(request)
        token = request.headers.get('x-koredocs-token', '')
        auth = request.headers.get('authorization', '')
        if auth.lower().startswith('bearer '):
            token = auth[7:].strip()
        if token != self._api_token:
            return Response('Unauthorized', status_code=401)
        return await call_next(request)
