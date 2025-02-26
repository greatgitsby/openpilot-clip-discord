#!/usr/bin/env python3

import asyncio
import discord
import os
import re
import replicate
import replicate.prediction
import requests

from abc import ABC, abstractmethod
from clipper.route_parser import parse_route_or_url, RouteParserException
from io import BytesIO
from urllib.parse import urlparse

link_regex = re.compile(r'https?://\S+')

class ReplayException(Exception):
    pass


class OpenpilotClipAsyncWorker(ABC):
    default_start_seconds = 0
    default_length_seconds = 10 

    @abstractmethod
    def start(self):
        pass

class OpenpilotClipAsyncProcessor(ABC):

    @abstractmethod
    def process(self, route_url: str):
        pass

class DiscordOpenpilotClipAsyncProcessor(OpenpilotClipAsyncProcessor):
    channel: discord.TextChannel
    worker: OpenpilotClipAsyncWorker

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel
    
    async def process(self, route_url: str):
        # Create an initial embed for processing status
        embed = discord.Embed(
            color=discord.Color.blue()
        )
        embed.add_field(name='Route', value=route_url, inline=False)
        embed.add_field(name='Status', value='Starting job (may take some time...)', inline=False)
        msg = await self.channel.send(embed=embed)
        
        try:
            worker = ReplicateClipWorker(route_url=route_url)
            worker.start()

            prev_status = worker.get_status()
            while not worker.is_complete():
                if prev_status != worker.get_status():
                    status_msg = worker.get_status() 
                    if worker.get_status() == 'processing':
                        status_msg = f'Processing (will take at least {worker.length_seconds / 60:.2f} minutes)'
                    embed.set_field_at(1, name='Status', value=status_msg)
                    await msg.edit(embed=embed)
                prev_status = worker.get_status()
                await asyncio.sleep(3)
                worker.update()

            print(f'finished with status {worker.get_status()}')
            
            if worker.succeeded():
                embed.set_field_at(1, name='Status', value='Uploading result')
                await msg.edit(embed=embed)
                discord_file = discord.File(worker.output(), f'{worker.get_route()}.mp4')
                embed.color = discord.Color.green()
                embed.set_field_at(1, name='Status', value='Finished')
                await msg.add_files(discord_file)
                await msg.edit(embed=embed)
            else:
                raise ReplayException(worker.get_error())
        except RouteParserException as e:
            error_embed = discord.Embed(
                title="Route Error",
                description=str(e),
                color=discord.Color.red()
            )
            error_embed.add_field(name='Route', value=route_url)
            await msg.edit(embed=error_embed)
        except ReplayException as e:
            # Create an error embed with the specific exception
            error_embed = discord.Embed(
                title="Replay Failed",
                description=f'```\n{str(e)}\n```',
                color=discord.Color.red()
            )
            error_embed.add_field(name='Route', value=route_url)
            await msg.edit(embed=error_embed)
        except Exception as e:
            # Create a generic error embed
            error_embed = discord.Embed(
                title="Unknown Error",
                description="An unknown error occurred. Please report to developers.",
                color=discord.Color.dark_red()
            )
            error_embed.add_field(name='Route', value=route_url)
            await msg.edit(embed=error_embed)
            print('unknown error', e)


class ReplicateClipWorker(OpenpilotClipAsyncWorker):
    model = 'nelsonjchen/op-replay-clipper'
    version = '30bc13ee7daf2087e8299b5beb7a81d3b11d09890279cbab05e73f81c6d3192f' 

    job: replicate.prediction.Prediction
    route_url: str
    route: str
    start_seconds: int
    length_seconds: int

    def __init__(self, route_url: str):
        super().__init__()

        route = parse_route_or_url(
            route_or_url=route_url,
            start_seconds=ReplicateClipWorker.default_start_seconds,
            length_seconds=ReplicateClipWorker.default_length_seconds,
            jwt_token=None
        )

        self.route_url = route_url
        self.route = route.route
        self.start_seconds = route.start_seconds
        self.length_seconds = route.length_seconds

    def is_valid_route(self):
        r = requests.get(f'https://api.comma.ai/v1/route/{self.route}')
        return r.status_code == 200
    
    def get_route(self):
        return self.route
    
    def start(self):
        model = replicate.models.get(self.model)
        version = model.versions.get(self.version)

        if not self.is_valid_route():
            raise ReplayException('The route is not public (or invalid). Please mark public in connect and try again.')

        job = replicate.predictions.create(
            version=version,
            input=self.get_model_input(),
        )

        self.job = job

    def get_status(self):
        return self.job.status
    
    def get_error(self) -> str | None:
        return self.job.error

    def update(self):
        self.job.reload()

    def is_complete(self):
        return self.job.status in ['succeeded', 'failed', 'canceled']
    
    def succeeded(self):
        return self.job.status == 'succeeded'

    def output(self):
        b = BytesIO()
        b.write(requests.get(self.job.output).content)
        b.seek(0)
        return b

    def get_model_input(self):
        return {
            "notes": "",
            "route": self.route,
            "metric": False,
            "fileSize": 10,
            "jwtToken": "",
            "fileFormat": "auto",
            "renderType": "ui",
            "smearAmount": 5,
            "startSeconds": self.start_seconds,
            "lengthSeconds": self.length_seconds,
            "speedhackRatio": 1,
            "forwardUponWideH": 2.2,
        }

# Function to create a progress bar
def create_progress_bar(progress, total_steps, bar_length=10):
    filled_length = int(bar_length * (progress / total_steps))
    bar = '▓' * filled_length + '░' * (bar_length - filled_length)
    return bar

async def process_clip(url: str, channel: discord.TextChannel):
    processor = DiscordOpenpilotClipAsyncProcessor(channel=channel)
    await processor.process(url)

class MyClient(discord.Client):
    async def on_ready(self):
        print('Logged on as', self.user)

    async def on_message(self, message):
        if message.author == self.user:
            return
        
        links = link_regex.findall(message.content)
        for link in links:
            if urlparse(link).hostname == 'connect.comma.ai':
                await process_clip(link, message.channel)

if __name__ == "__main__":
    discord_token = os.environ.get('DISCORD_TOKEN')
    if discord_token is None:
        raise EnvironmentError('Missing discord token')

    intents = discord.Intents.default()
    intents.messages = True
    intents.message_content = True

    client = MyClient(intents=intents)
    client.run(discord_token)