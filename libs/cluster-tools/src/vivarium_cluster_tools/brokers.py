import math
import os
import shutil
import socket
import subprocess
import tempfile

try:
    import drmaa
except RuntimeError:
    sge_cluster_name = os.environ['SGE_CLUSTER_NAME']
    os.environ['DRMAA_LIBRARY_PATH'] = f'/usr/local/UGE-{sge_cluster_name}/lib/lx-amd64/libdrmaa.so'
    import drmaa


from .utils import get_random_free_port, get_cluster_name

import logging
_log = logging.getLogger(__name__)

MEMORY_PER_CLUSTER_SLOT = 2.5
JOB_NAME = 'vivarium'
WORKER = 'vivarium_cluster_tools.worker.ResilientWorker'
RETRY_HANDLER = 'vivarium_cluster_tools.worker.retry_handler'


class RedisBroker:
    def __init__(self):
        self.hostname = None
        self.port = None
        self._process = None

    def initialize(self):
        self.hostname = socket.gethostname()
        self.port = get_random_free_port()
        _log.info('Starting Redis Broker at %s:%s', self.hostname, self.port)

        try:
            self._process = subprocess.Popen(
                [f"echo -e 'timeout 2\nprotected-mode no\nport {self.port}' | redis-server -"],
                shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError or ImportError:
            raise OSError("Redis server not found. Try `conda install redis`")

    @property
    def url(self):
        if self.hostname is None or self.port is None:
            raise AttributeError('No url available.')

        return f'redis://{self.hostname}:{self.port}'

    def cleanup(self):
        self._process.kill()


class ClusterBroker:

    def __init__(self, project, peak_memory, max_retries):
        self._drmaa_session = drmaa.Session()
        project_flag = f' -P {project}' if get_cluster_name() == 'prod' else ''
        num_slots = int(math.ceil(peak_memory/MEMORY_PER_CLUSTER_SLOT))
        self._preamble = (f'-w n -q all.q -l m_mem_free={peak_memory}G '
                          + f'-N {JOB_NAME} -pe multislot {num_slots}{project_flag}')

        self._max_retries = max_retries
        self._template = None
        self._launcher_name = None
        self._array_job_id = None

    def initialize(self, worker_log_directory, database_broker_url):
        self._drmaa_session.initialize()
        self._launcher_name = self._create_launcher_script(worker_log_directory, database_broker_url)

        self._template = self._drmaa_session.createJobTemplate()
        self._template.workingDirectory = os.getcwd()
        self._template.remoteCommand = shutil.which('sh')
        self._template.args = [self._launcher_name]
        self._template.jobEnvironment = {
            'LC_ALL': 'en_US.UTF-8',
            'LANG': 'en_US.UTF-8',
            'SGE_CLUSTER_NAME': os.environ['SGE_CLUSTER_NAME'],
        }
        self._template.joinFiles = True
        self._template.nativeSpecification = self._preamble
        self._template.outputPath = ':/dev/null'

    def launch_array_job(self, number_of_jobs):
        job_ids = self._drmaa_session.runBulkJobs(self._template, 1, number_of_jobs, 1)
        self._array_job_id = job_ids[0].split('.')[0]

    @staticmethod
    def _create_launcher_script(worker_log_directory, database_broker_url):
        launcher = tempfile.NamedTemporaryFile(mode='w', dir='.', prefix='distributed_worker_launcher_',
                                               suffix='.sh', delete=False)
        launcher.write(
            f'''
            export CEAM_LOGGING_DIRECTORY={worker_log_directory}
            {shutil.which('rq')} worker --url {database_broker_url} --name ${{JOB_ID}}.${{SGE_TASK_ID}} --burst -w "{WORKER}" --exception-handler "{RETRY_HANDLER}" ceam

            ''')
        launcher.close()
        return launcher.name

    def cleanup(self):
        self._template.delete()
        os.remove(self._launcher_name)
        self._kill_jobs()
        self._drmaa_session.exit()

    def _kill_jobs(self):
        try:
            self._drmaa_session.control(self._array_job_id, drmaa.JobControlAction.TERMINATE)
        except drmaa.errors.InvalidJobException:
            # This is the case where all our workers have already shut down
            # on their own, which isn't actually an error.
            pass
