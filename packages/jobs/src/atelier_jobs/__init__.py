"""The job queue, the shows index and the chat tables in SQLite, shared by the API and the worker
(ADR-022/023/036)."""

from importlib.metadata import version

from atelier_jobs.chat import ChatEvent, ChatMessage, ChatStore, Proposal
from atelier_jobs.queue import GPU_KINDS, JOB_KINDS, Job, JobQueue, ShowEntry, index_commits

__version__ = version("atelier-jobs")

__all__ = [
    "GPU_KINDS",
    "JOB_KINDS",
    "ChatEvent",
    "ChatMessage",
    "ChatStore",
    "Job",
    "JobQueue",
    "Proposal",
    "ShowEntry",
    "index_commits",
]
