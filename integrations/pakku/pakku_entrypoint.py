"""Run the original Misaka ASGI app with separate authenticated output extensions."""
import socket
import uvicorn
from src.main import app
from src.core import settings
from src.api.ui.pakku_config import router
from pakku_gateway import PakkuOutputMiddleware
from src.core.pakku_recent import RecentCommentProvider

app.state.pakku_recent_provider = RecentCommentProvider()
app.state.pakku_recent_provider.install(app)
app.include_router(router, prefix='/api/ui', include_in_schema=False)
app.add_middleware(PakkuOutputMiddleware, owner_app=app)

if __name__ == '__main__':
    ipv6 = getattr(settings.server, 'ipv6', True)
    if ipv6:
        original_bind = socket.socket.bind

        def dual_stack_bind(sock, address):
            if sock.family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            return original_bind(sock, address)

        socket.socket.bind = dual_stack_bind
    uvicorn.run(app, host='::' if ipv6 else settings.server.host, port=settings.server.port)
