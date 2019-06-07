from enum import Enum
import json

from loguru import logger
from pathlib import Path
import pandas as pd
from typing import Union


class LogType(Enum):

    WORKER_START = 'started, version'
    REDIS_CONNECTION = 'Listening on vivarium'
    NODE = 'Launching new job'
    WORKER_DONE = 'done, quitting'
    WORKER_KILLED = 'Killing worker'

    SIM_STARTING = 'Starting job'
    SIM_SETUP_COMPLETE = 'Simulation setup complete'
    SIM_LOOP_COMPLETE = 'Simulation main loop completed'
    SIM_AVG_STEP_LENGTH = 'Average step length'
    SIM_RUN_TIME_COMPLETE = 'Total simulation run time'
    SIM_ENDING = 'Exiting job'

    NON_ESSENTIAL = ''

    def __init__(self, str_match):
        self.str_match = str_match

    @classmethod
    def first_message(cls):
        return cls.WORKER_START

    def next_acceptable_log_types(self):
        next_types = {self.WORKER_START: [self.REDIS_CONNECTION, self.WORKER_KILLED],
                      self.REDIS_CONNECTION: [self.NODE, self.WORKER_DONE, self.WORKER_KILLED],
                      self.NODE: [self.SIM_STARTING, self.WORKER_KILLED],
                      self.WORKER_DONE: [],
                      self.WORKER_KILLED: [],
                      self.SIM_STARTING: [self.SIM_SETUP_COMPLETE, self.WORKER_KILLED],
                      self.SIM_SETUP_COMPLETE: [self.SIM_LOOP_COMPLETE, self.WORKER_KILLED],
                      self.SIM_LOOP_COMPLETE: [self.SIM_AVG_STEP_LENGTH, self.WORKER_KILLED],
                      self.SIM_AVG_STEP_LENGTH: [self.SIM_RUN_TIME_COMPLETE, self.WORKER_KILLED],
                      self.SIM_RUN_TIME_COMPLETE: [self.SIM_ENDING, self.WORKER_KILLED],
                      self.SIM_ENDING: [self.SIM_STARTING, self.WORKER_DONE, self.NODE, self.WORKER_KILLED],
                      self.NON_ESSENTIAL: [self.WORKER_START, self.REDIS_CONNECTION,  self.NODE, self.WORKER_DONE,
                                           self.SIM_STARTING, self.SIM_SETUP_COMPLETE, self.SIM_LOOP_COMPLETE,
                                           self.SIM_AVG_STEP_LENGTH, self.SIM_RUN_TIME_COMPLETE, self.SIM_ENDING,
                                           self.NON_ESSENTIAL, self.WORKER_KILLED]}
        return next_types[self]

    @classmethod
    def classify_log(cls, log_msg):
        # non-essential will match everything, so exclude it unless no other options
        possible = [l for l in cls if l.str_match in log_msg and l is not cls.NON_ESSENTIAL]
        return possible[0] if possible else cls.NON_ESSENTIAL


class LogMessage:

    def __init__(self, log: dict):
        self.time = pd.Timestamp(log['record']['time']['repr'])
        self.level = log['record']['level']['name']
        self.message = log['record']['message']
        self.exception = log['record']['exception']
        self.log_type = LogType.classify_log(self.message)


class WorkerLog:

    sim_message_data_map = {LogType.SIM_SETUP_COMPLETE: 'setup_time',
                            LogType.SIM_LOOP_COMPLETE: 'loop_time',
                            LogType.SIM_AVG_STEP_LENGTH: 'step_time',
                            LogType.SIM_RUN_TIME_COMPLETE: 'total_time'}

    def __init__(self, file: Path):
        self.file = file
        name = file.stem.split('.')  # TODO: validate name is <sge job id>.<task id>.log
        self.sge_job_id = int(name[0])
        self.task_id = int(name[1])

    def _load_messages(self):
        with self.file.open('r') as f:
            messages = [LogMessage(json.loads(line)) for line in f]
        return messages

    def summarize(self) -> (pd.DataFrame, pd.DataFrame):
        idx = pd.MultiIndex.from_tuples([(self.sge_job_id, self.task_id)], names=('sge_job_id', 'task_id'))
        worker = pd.DataFrame({'worker_start_time': pd.NaT, 'worker_end_time': pd.NaT, 'node': None,
                               'worker_killed': 0, 'jobs_assigned': 0, 'jobs_completed': 0,
                               'num_exceptions': 0, 'abnormalities': [[]]},
                              index=idx)

        template_sim = pd.DataFrame({'setup_time': None, 'loop_time': None, 'step_time': None,
                                  'total_time': None, 'job_started': [0], 'job_complete': [0]},
                                 index=idx)
        sims = [pd.DataFrame()]
        sim = template_sim
        expected_msg_types = [LogType.first_message()]

        messages = self._load_messages()
        for i, msg in enumerate(messages):

            if msg.exception:
                warning_msg = f'{msg.exception["type"]} found in log file {self.file.stem} at line {i}.'
                logger.debug(warning_msg)
                worker['num_exceptions'] += 1

            if msg.log_type is LogType.NON_ESSENTIAL:  # no need to check
                continue

            if msg.log_type not in expected_msg_types:
                # TODO: actually handle this and give user exploration options instead of just warning
                warning_msg = f'Unexpected message type {msg.log_type} encountered at line {i}. ' \
                    f'Expecting one of: {[t.name for t in expected_msg_types]}.'
                logger.debug(f'In {self.file.stem}: ' + warning_msg)
                worker['abnormalities'] += [warning_msg]

            if msg.log_type is LogType.WORKER_START:
                worker['worker_start_time'] = msg.time
            elif msg.log_type is LogType.NODE:
                worker['node'] = msg.message.split()[-1]
            elif msg.log_type is LogType.WORKER_KILLED:
                worker['abnormalities'] += [f'Worker killed with message: {msg.message}.']
                worker['worker_killed'] = 1
            elif msg.log_type is LogType.SIM_STARTING:
                worker['jobs_assigned'] += 1
                sim['job_started'] = 1
            elif msg.log_type is LogType.SIM_ENDING:
                sim['job_complete'] = 1
                worker['jobs_completed'] += 1
                sims.append(sim)
                sim = template_sim
            elif msg.log_type in self.sim_message_data_map:  # pull out timing info from msg and record
                sim[self.sim_message_data_map[msg.log_type]] = float(msg.message.split()[-2])

            expected_msg_types = msg.log_type.next_acceptable_log_types()

        if messages:
            worker['worker_end_time'] = pd.Timestamp(messages[-1].time)

            if expected_msg_types:
                warning_msg = f'Log file ended unexpectedly. Expecting additional log message(s) ' \
                    f'of type {" or ".join(expected_msg_types)}.'
                logger.debug(f'In {self.file.stem}: ' + warning_msg)
                worker['abnormalities'] += [warning_msg]

            # log has ended without exit msg for last sim (e.g., worker was killed in middle of sim job)
            if not sim.equals(template_sim):
                sims.append(sim)

        else:
            worker['abnormalities'] += [f'Empty log file.']
            logger.debug(f'Empty log file encountered: {self.file.stem}.')

        return worker, pd.concat(sims)


def parse_log_directory(input_directory: Union[Path, str], output_directory: Union[Path, str]):
    input_directory, output_directory = Path(input_directory), Path(output_directory)
    log_files = [f for f in input_directory.iterdir() if f.suffix == '.log']

    worker_data = []
    sim_data = []
    for i, file in enumerate(log_files):
        logger.info(f'Parsing file {i+1} of {len(log_files)}.')
        w = WorkerLog(file)
        w_d, s_d = w.summarize()
        worker_data.append(w_d)
        sim_data.append(s_d)

    worker_data = pd.concat(worker_data)
    sim_data = pd.concat(sim_data)

    out_file = output_directory / 'log_summary.hdf'
    worker_data.to_hdf(out_file, key='worker_data')
    sim_data.to_hdf(out_file, key='sim_data')

    logger.info(f'All log files successfully parsed. Summary hdf can be found at {out_file}.')
