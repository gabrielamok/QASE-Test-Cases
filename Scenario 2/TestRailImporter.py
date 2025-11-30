
from .support import ConfigManager, Logger, Mappings, ThrottledThreadPoolExecutor, Pools
from .service import QaseService, TestrailService, QaseScimService
from .entities import Users, Fields, Projects, Suites, Cases, Runs, Milestones, Configurations, Attachments, SharedSteps
from concurrent.futures import ThreadPoolExecutor

# >>> MERGE: import HTML table converter -> Markdown <<<
# Place the module according to your project path. If it is in another package,
# adjust 'utils.html_table_converter' to the correct path.
from utils.html_table_converter import convert_testrail_tables_to_markdown


# >>> MERGE: mapping function that sanitizes rich fields with tables <<<
def _map_case_payload(tr_case: dict) -> dict:
    """
    Applies convert_testrail_tables_to_markdown on rich fields coming from TestRail
    (descriptions, pre/post-conditions, steps, expected etc.) and returns the payload
    ready to send to Qase.

    Note: if your TestRail field schema uses other keys (e.g., 'refs', 'custom_steps_separated'),
    adjust the names below as needed.
    """
    def _clean(v: str) -> str:
        return convert_testrail_tables_to_markdown(v or '')

    mapped = {
        'title': tr_case.get('title', ''),
        'description': _clean(tr_case.get('custom_description', '')),
        'preconditions': _clean(tr_case.get('custom_preconds', '')),
        'postconditions': _clean(tr_case.get('custom_postconds', '')),
# If you also send steps/expected as a single text, clean them here:
        'steps': _clean(tr_case.get('custom_steps', '')),
        'expected_result': _clean(tr_case.get('custom_expected', '')),
# ... include other fields as needed
    }

    return mapped


class TestRailImporter:
    def __init__(self, config: ConfigManager, logger: Logger) -> None:
        self.pools = Pools(
            qase_pool=ThrottledThreadPoolExecutor(max_workers=8, requests=230, interval=10),
            tr_pool=ThreadPoolExecutor(max_workers=8),
        )

        self.logger = logger
        self.config = config
        self.qase_scim_service = None
        
        self.qase_service = QaseService(config, logger)
        if config.get('qase.scim_token'):
            self.qase_scim_service = QaseScimService(config, logger)

        self.testrail_service = TestrailService(config, logger)

        self.active_project_code = None

        self.mappings = Mappings(self.config.get('users.default'))

    def start(self):
        # Step 1. Build users map (if migration is enabled)
        if self.config.get('users.migrate') is not False:
            self.mappings = Users(
                self.qase_service,
                self.testrail_service,
                self.logger,
                self.mappings,
                self.config,
                self.pools,
                self.qase_scim_service,
            ).import_users()
        else:
            self.logger.log("User migration is disabled by configuration")

        # Step 2. Import project and build projects map
        self.mappings = Projects(
            self.qase_service, 
            self.testrail_service, 
            self.logger, 
            self.mappings,
            self.config,
            self.pools,
        ).import_projects()

        # Step 3. Import attachments
        self.mappings = Attachments(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.config,
            self.pools,
        ).import_all_attachments()

        # Step 4. Import custom fields
        self.mappings = Fields(
            self.qase_service, 
            self.testrail_service, 
            self.logger, 
            self.mappings,
            self.config,
            self.pools,
        ).import_fields()

        # Step 5. Import projects data in parallel
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for project in self.mappings.projects:
                # Submit each project import to the thread pool
                future = executor.submit(self.import_project_data, project)
                futures.append(future)

            # Wait for all futures to complete
            for future in futures:
                # This will also re-raise any exceptions caught during execution of the callable
                future.result()

        self.mappings.stats.print()
        self.mappings.stats.save(str(self.config.get('prefix')))
        self.mappings.stats.save_xlsx(str(self.config.get('prefix')))

    def import_project_data(self, project):
        self.logger.print_group(
            f'Importing project: {project["name"]}'
            + (' (' + project['suite_title'] + ')' if 'suite_title' in project else '')
        )

        self.mappings = Configurations(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.pools,
        ).import_configurations(project)
        
        self.mappings = SharedSteps(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.pools,
        ).import_shared_steps(project)

        self.mappings = Milestones(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
        ).import_milestones(project)

        self.mappings = Suites(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.config,
            self.pools,
        ).import_suites(project)

# >>> MERGE: pass the mapper for table conversion before sending to Qase <<<
# If your Cases.import_cases accepts 'payload_mapper', use like this:
        Cases(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.config,
            self.pools,
        ).import_cases(project, payload_mapper=_map_case_payload)

        Runs(
            self.qase_service,
            self.testrail_service,
            self.logger,
            self.mappings,
            self.config,
            project,
            self.pools,
        ).import_runs()
