"""Small teaching wrapper around this Rakazo version's authenticated HTTP API.

Copied from the CS239 tutorial bundle (cs239-multi-agent-tutorial/examples/
movie_night/rakazo.py, course-authored, October 2026) so the project can create
its bots the same way the lecture did. This is not an official Rakazo SDK or an
A2A server. Rakazo must already run.

Changes from the tutorial copy: this header, the ``list_bots()``,
``model_credentials()``, ``update_bot()`` and ``always_allow_tool()`` helpers,
and ``create_team()`` skipping
``models/connect`` for a model without a base URL (a subscription connected in
the Rakazo UI, e.g. ``openai-codex``). Kept minimal on purpose so the API helper stays
comparable with the course material; if your Rakazo build exposes different
routes, this is the one file to adapt.
"""
from dataclasses import asdict, dataclass, field
import httpx


@dataclass(frozen=True)
class ModelConnection:
    provider: str
    model_id: str
    base_url: str
    api_key: str = field(default='', repr=False)


@dataclass(frozen=True)
class MCPConnection:
    name: str
    slug: str
    endpoint: str


@dataclass(frozen=True)
class BotProfile:
    name: str
    instructions: str
    model: ModelConnection
    mcp: tuple[MCPConnection, ...] = ()


class Rakazo:
    def __init__(self, url: str, *, origin: str | None = None):
        # Use the web origin when its API is reached on a different port.
        self.http = httpx.Client(base_url=url.rstrip('/'), timeout=30,
                                 headers={'Origin': origin or url.rstrip('/')})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.http.close()

    def login(self, email: str, password: str):
        response = self.http.post('/api/auth/sign-in/email',
                                  json={'email': email, 'password': password})
        if not response.is_success:
            raise RuntimeError(f'Login failed (HTTP {response.status_code}). Check account and origin.')

    def rpc(self, route: str, data: dict | None = None):
        response = self.http.post('/rpc/' + route, json={'json': data or {}})
        if not response.is_success:
            # Do not print response bodies: provider errors can echo credentials.
            raise RuntimeError(f'{route} failed (HTTP {response.status_code}). Check the Rakazo server logs.')
        body = response.json()
        if 'json' not in body:
            raise RuntimeError(f'{route} returned an unexpected response')
        return body['json']

    def create_team(self, *profiles: BotProfile) -> dict[str, str]:
        """Create new bots. Refuse duplicate names; do not silently overwrite a workspace."""
        names = [p.name for p in profiles]
        if len(set(names)) != len(names):
            raise ValueError('Bot names must be distinct')
        existing = {b['name'] for b in self.rpc('bots/list')}
        if existing.intersection(names):
            raise ValueError('These bot names already exist. Use a fresh workspace or edit them in Rakazo.')
        servers = {s['slug']: s for s in self.rpc('mcp/servers/list')}
        endpoints = {}
        for p in profiles:
            for mcp in p.mcp:
                if mcp.slug in endpoints and endpoints[mcp.slug] != mcp.endpoint:
                    raise ValueError('Conflicting MCP endpoints for the same slug')
                endpoints[mcp.slug] = mcp.endpoint
                if mcp.slug in servers and servers[mcp.slug]['endpoint'] != mcp.endpoint:
                    raise ValueError('Existing MCP slug points to a different endpoint')
        bots = {}
        for p in profiles:
            # A subscription provider (e.g. openai-codex) is connected once by signing in
            # through the Rakazo UI; only key/endpoint providers are connected here.
            if p.model.base_url:
                self.rpc('models/connect', {'provider': p.model.provider, 'modelId': p.model.model_id,
                         'baseUrl': p.model.base_url, 'apiKey': p.model.api_key})
            bot = self.rpc('bots/create', {'name': p.name, 'title': '',
                           'description': p.instructions, 'instructions': p.instructions,
                           'notifyOnFinish': False})
            self.rpc('bots/update', {'botId': bot['id'], 'modelProvider': p.model.provider,
                                    'modelId': p.model.model_id})
            for mcp in p.mcp:
                if mcp.slug not in servers:
                    servers[mcp.slug] = self.rpc('mcp/servers/create', {
                        **asdict(mcp), 'transport': 'streamable_http'})
                self.rpc('mcp/assignments/approve', {'botId': bot['id'],
                         'serverId': servers[mcp.slug]['id']})
            bots[p.name] = bot['id']
        return bots

    def list_bots(self):
        return self.rpc('bots/list')

    def model_credentials(self):
        return self.rpc('models/credentials')

    def update_bot(self, bot_id: str, profile: BotProfile):
        """Push a profile's instructions and model to an existing bot. Bots may not rewrite
        their own instructions, so a run always uses the prompt that is in the repository."""
        return self.rpc('bots/update', {'botId': bot_id, 'description': profile.instructions,
                                        'instructions': profile.instructions,
                                        'selfUpdateInstructions': False,
                                        'modelProvider': profile.model.provider,
                                        'modelId': profile.model.model_id})

    def always_allow_tool(self, tool: str):
        """Skip Rakazo's per-call human review for one tool (``mcp__<slug>__<name>``)."""
        return self.rpc('approvalRules/set', {'effect': 'always_allow', 'matchKind': 'tool',
                                              'matchValue': tool})

    def send(self, bot_id: str, message: str):
        """Start an asynchronous run; this returns a run handle, not its final answer."""
        return self.rpc('threads/send', {'botId': bot_id, 'text': message})

    def conversation(self, bot_id: str):
        return self.rpc('threads/get', {'botId': bot_id})
