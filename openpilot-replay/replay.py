#!/usr/bin/env python3

import discord
import os
import re
import replicate
import replicate.prediction
import requests
import asyncio
from clipper.route_parser import parse_route_or_url
from abc import ABC, abstractmethod
from io import BytesIO

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
        msg = await self.channel.send(f'Processing {route_url}')
        try:
            worker = ReplicateClipWorker(route_url=route_url)
            worker.start()

            print('waiting')
            while not worker.is_complete():
                print(worker.get_status())
                await msg.edit(content=worker.get_status())
                await asyncio.sleep(5)
                worker.update()

            print(f'finished with status {worker.get_status()}')
            
            if worker.succeeded():
                discord_file = discord.File(worker.output(), 'output.mp4')
                await msg.add_files(discord_file)
                await msg.edit(content='')
            else:
                raise ReplayException(f'replay failed:\n\n```\n{worker.get_error()}\n```')
        except ReplayException as e:
            await msg.edit(content=str(e))
        except Exception as e:
            await msg.edit(content='unknown error, report to developers')
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
    
    def start(self):
        model = replicate.models.get(self.model)
        version = model.versions.get(self.version)

        if not self.is_valid_route():
            raise ReplayException(f'[Route]({self.route_url}) is not public (or invalid). Please mark public in connect and try again.')

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

# Simulate work that takes time
async def do_work(url: str, channel: discord.TextChannel):
    total_steps = 3
    progress_bar_length = 10  # Length of the progress bar
    processor = DiscordOpenpilotClipAsyncProcessor(channel=channel)

    await processor.process(url)

    # # Create an initial embed
    # embed = discord.Embed(
    #     title="Work in Progress",
    #     description=f"Job {job.id}",
    #     color=discord.Color.blue()
    # )
    # embed.add_field(name="Progress", value=create_progress_bar(0, total_steps, progress_bar_length), inline=False)
    # embed.add_field(name="Status", value=job.status, inline=False)

    # # Send the initial embed message
    # progress_message = await channel.send(embed=embed)

    # last_status = job.status
    # while job.status not in ['succeeded', 'failed', 'canceled']:
    #     print(f'status: {job.status}')
    #     status = job.status

    #     if last_status != status:
    #         if status == 'starting':
    #             n = 1
    #         elif status == 'processing':
    #             n = 2
    #         embed.set_field_at(0, name="Progress", value=create_progress_bar(n, total_steps, progress_bar_length), inline=False)
    #         embed.set_field_at(1, name="Status", value=job.status, inline=False)
    #         await progress_message.edit(embed=embed)

    #     last_status = job.status
    #     await asyncio.sleep(5)
    #     job.reload()

    # if job.status == 'succeeded':
    #     job.wait()
    #     file_ptr = job.output

    #     # Create a text file with results
    #     with open("output.mp4", "wb") as file:
    #         file.write(requests.get(file_ptr).content)

    #     # Upload the text file
    #     with open("output.mp4", "rb") as file:
    #         d_f = discord.File(file, "output.mp4")
    #         await progress_message.add_files(d_f)
    #         await progress_message.edit(content='', embed=None)
    # else:
    #     # Notify the user the job has failed or was canceled.
    #     embed.title = "Work Complete"
    #     embed.description = "The work has been completed."
    #     embed.set_field_at(0, name="Progress", value=create_progress_bar(total_steps, total_steps, progress_bar_length), inline=False)
    #     embed.set_field_at(1, name="Status", value=job.status, inline=False)
    #     await progress_message.edit(embed=embed)


class MyClient(discord.Client):
    async def on_ready(self):
        print('Logged on as', self.user)

    async def on_message(self, message):
        if message.author == self.user:
            return
        
        links = link_regex.findall(message.content)
        for link in links:
            await do_work(link, message.channel)

if __name__ == "__main__":
    discord_token = os.environ.get('DISCORD_TOKEN')
    if discord_token is None:
        raise EnvironmentError('Missing discord token')

    intents = discord.Intents.default()
    intents.messages = True
    intents.message_content = True

    client = MyClient(intents=intents)
    client.run(discord_token)

    # download_segments(
    #     '/tmp/data',
    #     route.route,
    #     10,
    #     route.start_seconds,
    #     route.length_seconds,
    #     ['cameras'],
    #     None,
    # )
