"""
Delete job manager module that provides the interface for submitting and
checking the execution status of data delete jobs running in a remote compute
environment (ChRIS / pfcon interface) as well as deleting them when finished.
"""

import logging
import json

from pfconclient.client import JobType
from pfconclient.exceptions import PfconRequestException

from core.utils import json_zip2str
from plugininstances.models import PluginInstance
from .abstractjobs import PluginInstanceJob


logger = logging.getLogger(__name__)


class PluginInstanceDeleteJob(PluginInstanceJob):
    """
    ``PluginInstanceDeleteJob`` provides a concrete implementation for managing a remote
    data delete job related to a plugin instance. This job runs AFTER the plugin instance
    has reached a terminal status. It must NEVER modify ``self.c_plugin_inst.status``.
    It uses ``remote_cleanup_status`` as its state tracker.
    """

    def run(self):
        """
        Run the plugin instance data delete job via a call to a remote pfcon service.
        """
        if self.c_plugin_inst.remote_cleanup_status != 'deletingData':
            return

        job_id = self.str_job_id
        pfcon_url = self.pfcon_client.url

        logger.info(f'Submitting data delete job {job_id} to pfcon url '
                    f'-->{pfcon_url}<--')
        try:
            d_resp = self._submit(JobType.DELETE, job_id, {})
        except PfconRequestException as e:
            logger.error(f'[CODE01,{job_id}]: Error submitting data delete job to pfcon '
                         f'url -->{pfcon_url}<--, detail: {str(e)}')

            self._increment_retry_or_fail()
        else:
            logger.info(f'Successfully submitted data delete job {job_id} to pfcon url '
                        f'-->{pfcon_url}<--, response: {json.dumps(d_resp, indent=4)}')

            # update the job summary
            self.c_plugin_inst.summary = self.get_job_status_summary(d_resp)
            self.c_plugin_inst.raw = json_zip2str(d_resp)
            self.c_plugin_inst.save(update_fields=['summary', 'raw'])

    def check_exec_status(self):
        """
        Check a plugin instance data delete job's execution status. The job's execution
        status is fetched from the remote. Uses retry count instead of timeout to bound
        retries.
        """
        if self.c_plugin_inst.remote_cleanup_status != 'deletingData':
            return self.c_plugin_inst.remote_cleanup_status

        job_id = self.str_job_id
        pfcon_url = self.pfcon_client.url

        logger.info(f'Sending job status request to pfcon url -->{pfcon_url}<-- for '
                    f'delete job {job_id}')
        try:
            d_resp = self._get_status(JobType.DELETE, job_id)
        except PfconRequestException as e:
            logger.error(f'[CODE02,{job_id}]: Error getting delete job status at '
                         f'pfcon url -->{pfcon_url}<--, detail: {str(e)}')
            # try resubmission
            self.run()
            return self.c_plugin_inst.remote_cleanup_status

        logger.info(f'Successful job status response from pfcon url -->{pfcon_url}<--'
                    f' for delete job {job_id}: {json.dumps(d_resp, indent=4)}')

        status = d_resp['compute']['status']
        logger.info(f'Current delete job {job_id} remote status = {status}')

        summary = self.get_job_status_summary(d_resp)
        self.c_plugin_inst.summary = summary
        raw = json_zip2str(d_resp)
        self.c_plugin_inst.raw = raw

        # only update (atomically) if remote_cleanup_status='deletingData'
        PluginInstance.objects.filter(
            id=self.c_plugin_inst.id,
            remote_cleanup_status='deletingData').update(summary=summary, raw=raw)

        if status == 'finishedSuccessfully':
            self.handle_finished_successfully_status()
        elif status == 'finishedWithError':
            self.handle_finished_with_error_status()
        elif status == 'undefined':
            self.handle_undefined_status()

        return self.c_plugin_inst.remote_cleanup_status

    def cancel_exec(self):
        """
        Cancel a plugin instance data delete job execution. Sets remote_cleanup_status
        to 'failed'. Does NOT modify plugin instance status.
        """
        self.c_plugin_inst.remote_cleanup_status = 'failed'
        self.c_plugin_inst.save(update_fields=['remote_cleanup_status'])

    def delete(self):
        """
        Delete a plugin instance data delete job from the remote compute. It connects to
        the remote service to delete the job.
        """
        pfcon_url = self.pfcon_client.url
        job_id = self.str_job_id
        logger.info(f'Deleting data delete job {job_id} from pfcon at url '
                    f'-->{pfcon_url}<--')
        try:
            self._delete(JobType.DELETE, job_id)
        except PfconRequestException as e:
            logger.error(f'[CODE12,{job_id}]: Error deleting data delete job from '
                             f'pfcon at url -->{pfcon_url}<--, detail: {str(e)}')
            self.c_plugin_inst.error_code = 'CODE12'
        else:
            logger.info(f'Successfully deleted data delete job {job_id} from pfcon at '
                        f'url -->{pfcon_url}<--')
            if self.c_plugin_inst.error_code == 'CODE12':
                self.c_plugin_inst.error_code = ''

    def handle_finished_successfully_status(self):
        """
        Handle the 'finishedSuccessfully' status returned by the remote compute.
        Transitions to 'deletingContainers' phase and attempts to delete all remote
        containers.
        """
        job_id = self.str_job_id
        logger.info(f'Delete data job {job_id} finished successfully')

        self.c_plugin_inst.remote_cleanup_status = 'deletingContainers'
        self.c_plugin_inst.save(update_fields=['remote_cleanup_status'])

        if self.delete_all_remote_containers():
            self.c_plugin_inst.remote_cleanup_status = 'complete'
        # else: stays 'deletingContainers' for periodic task retry

        self.c_plugin_inst.save(update_fields=['remote_cleanup_status'])

    def handle_finished_with_error_status(self):
        """
        Handle the 'finishedWithError' status returned by the remote compute.
        Retries the delete job if under max retries, otherwise marks as failed.
        """
        job_id = self.str_job_id
        logger.error(f'[CODE18,{job_id}]: Error while running delete data job, remote '
                     f'compute returned finishedWithError status for job {job_id}')

        self._increment_retry_or_fail()

    def handle_undefined_status(self):
        """
        Handle the 'undefined' status returned by the remote compute.
        Retries the delete job if under max retries, otherwise marks as failed.
        """
        job_id = self.str_job_id
        logger.error(f'[CODE18,{job_id}]: Error while running delete data job, remote '
                     f'compute returned undefined status for job {job_id}')

        self._increment_retry_or_fail()

    def _increment_retry_or_fail(self):
        """
        Increment the remote cleanup retry count. If it exceeds the max, mark cleanup
        as failed. Otherwise resubmit the delete job.
        """
        self.c_plugin_inst.remote_cleanup_retry_count += 1

        if (self.c_plugin_inst.remote_cleanup_retry_count >
                PluginInstance.MAX_REMOTE_CLEANUP_RETRIES):
            
            logger.error(f'Delete job for plugin instance {self.c_plugin_inst.id} '
                         f'exceeded max retries, marking cleanup as failed')
            
            self.c_plugin_inst.remote_cleanup_status = 'failed'
            self.c_plugin_inst.save(update_fields=['remote_cleanup_status',
                                                    'remote_cleanup_retry_count'])
        else:
            self.c_plugin_inst.save(update_fields=['remote_cleanup_retry_count'])
            self.run()  # try resubmission
