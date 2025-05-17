import asyncio
import discord
import re
import os
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

from openpilot.tools.lib.route import Route


CONNECT_URL = 'https://connect.comma.ai'
MAX_CLIP_LEN_S = int(os.environ.get('MAX_CLIP_LEN', '30'))
WORKERS = int(os.environ.get('WORKERS', '1'))

link_regex = re.compile(r'https?://\S+')
route_regex = re.compile(r'\S{16}\/\S{8}--\S{10}')
route_with_time_regex = re.compile(r'\S{16}\/\S{8}--\S{10}\/\d+\/\d+')


def format_route(route_with_time: str) -> str:
  return f'[`{route_with_time}`]({CONNECT_URL}/{route_with_time})'


@dataclass
class ClipRequest:
  ctx: discord.ApplicationContext
  route: str
  title: str | None
  start_time: int
  end_time: int
  bookmark_time_sec: int | None = None

  @property
  def bookmark_time_str(self) -> str:
    if not self.is_bookmark:
      raise NotImplementedError('only bookmark requests have bookmark time')
    minutes = self.bookmark_time_sec // 60
    remaining_seconds = self.bookmark_time_sec % 60
    return f'{minutes:02d}:{remaining_seconds:02d}'

  @property
  def formatted_bookmark_time(self) -> str:
    return f'[`{self.bookmark_time_str}`]({CONNECT_URL}/{self.route_with_time})'

  @property
  def formatted_route(self) -> str:
    return format_route(self.route_with_time)

  @property
  def message_content(self) -> str:
    content = f'<@{self.ctx.interaction.user.id}> shared a clip: {self.formatted_route}'
    if self.is_bookmark:
      content += f', bookmarked at {self.bookmark_time_str}'
    return content

  @property
  def route_with_time(self) -> str:
    return f'{self.route}/{self.start_time}/{self.end_time}'

  @property
  def is_bookmark(self) -> bool:
    return self.bookmark_time_sec is not None

  @property
  def output_file_name(self) -> str:
    return f'{self.route.replace("/", "-")}.mp4'

  async def post_processing_message(self, content: str):
    if self.is_bookmark:
      await self.ctx.respond(content=content, ephemeral=True)
    else:
      await self.ctx.edit(content=content)

  async def post_success(self, file_path: Path):
    file = discord.File(file_path)
    view = VideoPreview(self, file)
    if self.is_bookmark:
      await self.ctx.respond(content=self.message_content, file=file, view=view, ephemeral=True)
    else:
      await self.ctx.edit(content=self.message_content, file=file, view=view)

  async def post_error(self, error_msg: str):
    error_content = f'clip of {self.formatted_route} failed due to unknown reason:\n\n```\n{error_msg}\n```'
    await self.post_processing_message(error_content)


bot = discord.Bot()
queue = asyncio.Queue[ClipRequest]()


class VideoPreview(discord.ui.View):
  def __init__(self, request: ClipRequest, vid: discord.File):
    super().__init__(timeout=None)
    self.request = request
    self.vid = vid

  @discord.ui.button(label='Post', style=discord.ButtonStyle.primary, emoji='▶️')
  async def post_button(self, button: discord.ui.Button, interaction: discord.Interaction):
    button.label = 'Posted'
    button.emoji = '✅'
    button.style = discord.ButtonStyle.green
    button.disabled = True
    
    await interaction.response.edit_message(view=self)
    await interaction.respond(content=self.request.message_content, file=self.vid)



def get_route(route: str) -> str | None:
  link = link_regex.match(route)
  if link:
    path = urlparse(link.group()).path
    route = route_regex.match(path[1:])
  else:
    route = route_regex.match(route)

  if not route:
    return None

  return route.group()


def get_route_and_time(route: str) -> tuple[str, int, int] | None:
  link = link_regex.match(route)
  if link:
    path = urlparse(link.group()).path
    route = route_with_time_regex.match(path[1:])
  else:
    route = route_with_time_regex.match(route)

  if not route:
    return None

  route = route.group()
  start_str, end_str = route.split('/')[2:]
  route = '/'.join(route.split('/')[:2])
  start, end = int(start_str), int(end_str)
  return route, start, end


def get_user_flags(route: str):
  route = Route(route)
  user_flags_at_time = []

  def process_segment(segment):
    for event in segment.events:
      if event['type'] == 'user_flag':
        time_ms = event['route_offset_millis']
        time_sec = round(time_ms / 1000)
        user_flags_at_time.append(time_sec)

  with ThreadPoolExecutor(max_workers=WORKERS * 8) as executor:
    executor.map(process_segment, route.segments)

  return sorted(user_flags_at_time)


async def process_clip(request: ClipRequest):
  print(f'{request.ctx.interaction.user.display_name} ({request.ctx.interaction.user.id}) clipping {request.route_with_time}' )
  if not request.is_bookmark:
    await request.post_processing_message(f'clipping {request.formatted_route}')
  try:
    with TemporaryDirectory() as temp_dir:
      path = Path(os.path.join(temp_dir, request.output_file_name)).resolve()
      args = ['openpilot/tools/clip/run.py', request.route_with_time, '-o', path, '-f', '9']
      if request.title:
        args.extend(['-t', request.title])
      proc = await asyncio.create_subprocess_exec('openpilot/.venv/bin/python3', *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

      stdout, stderr = await proc.communicate()
      if proc.returncode != 0:
        await request.post_error(stderr.decode())
      else:
        await request.post_success(discord.File(path))
  except Exception as e:
    print('error processing clip', str(e))
    await request.post_error(str(e))


async def worker(name: str):
  print(f'started worker {name}')
  while True:
    request = await queue.get()
    try:
      await process_clip(request)
    finally:
      queue.task_done()


@bot.command(
  description="clip an openpilot route",
  integration_types={
    discord.IntegrationType.guild_install,
    discord.IntegrationType.user_install,
  },
)
@discord.option("route", type=str, description='the route or connect URL with timing info', required=True)
@discord.option("title", type=str, description='an optional title to overlay', min_length=1, max_length=80, required=False)
async def clip(ctx: discord.ApplicationContext, route: str, title: str):
  await ctx.defer(ephemeral=True)
  parsed = get_route_and_time(route)

  if parsed is None:
    await ctx.respond(content='please enter a valid route or connect URL')
    return

  route, start, end = parsed
  if end - start > MAX_CLIP_LEN_S:
    await ctx.edit(content=f'cannot make a clip longer than {MAX_CLIP_LEN_S}s')
    return

  await ctx.edit(content=f'queued request, {queue.qsize()} in line ahead')
  await queue.put(ClipRequest(
    ctx=ctx,
    route=route,
    title=title,
    start_time=start,
    end_time=end,
  ))


@bot.command(
  description="clip your openpilot route bookmarks",
  integration_types={
    discord.IntegrationType.guild_install,
    discord.IntegrationType.user_install,
  },
)
@discord.option("route", type=str, description='the route or connect URL', required=True)
async def bookmarks(ctx: discord.ApplicationContext, route: str):
  if ctx.author.bot:
    return

  before_flag_buffer = 10
  after_flag_buffer = 5

  await ctx.defer(ephemeral=True)
  route = get_route(route)

  if route is None:
    await ctx.respond(content='please enter a valid route or connect URL')
    return

  flags = get_user_flags(route)
  if len(flags) == 0:
    await ctx.respond(content='no bookmarks found, try creating a /clip instead!', ephemeral=True)
    return

  msg = f'{len(flags)} bookmark{"" if len(flags) == 1 else "s"} during route {format_route(route)}, processing {"it" if len(flags) == 1 else "them"}...\n'

  clip_details = []
  for i in range(0, len(flags)):
    flag = flags[i]
    request = ClipRequest(
      ctx=ctx,
      route=route,
      title=None,
      start_time=flag-before_flag_buffer,
      end_time=flag+after_flag_buffer,
      bookmark_time_sec=flag
    )
    await queue.put(request)
    clip_details.append(request.formatted_bookmark_time)

  await ctx.respond(f'{msg}\n' + ', '.join(clip_details))


@bot.listen(once=True)
async def on_ready():
  for i in range(WORKERS):
    asyncio.create_task(worker(f'clip-worker-{i}'))


if __name__ == "__main__":
  discord_token = os.environ.get('DISCORD_TOKEN')
  if discord_token is None:
    raise EnvironmentError('Missing discord token')
  bot.run(discord_token)

