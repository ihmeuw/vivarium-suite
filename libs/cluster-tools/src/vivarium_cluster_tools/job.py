import logging
from time import time
from traceback import format_exc
from typing import Mapping

from rq import get_current_job


def job(parameters: Mapping, logging_directory: str, with_state_table: bool):
    input_draw = parameters['input_draw']
    random_seed = parameters['random_seed']
    component_config = parameters['simulation_configuration']
    branch_config = parameters['config']
    logging.info('Starting job: {}'.format((input_draw, random_seed, component_config, branch_config)))

    try:
        metrics, final_state = _run_simulation(parameters)
        return _format_output(metrics, final_state, parameters)

    except Exception as e:
        logging.exception('Unhandled exception in worker')
        job = get_current_job()
        job.meta['root_exception'] = format_exc()
        job.save_meta()
        raise
    finally:
        logging.info('Exiting job: {}'.format((input_draw, random_seed, component_config, branch_config)))


def _run_simulation(parameters):
    job_id = get_current_job().id
    branch_config = parameters['config']
    from vivarium.framework.engine import build_simulation_configuration, run, setup_simulation
    from vivarium.framework.components import load_component_manager

    config = build_simulation_configuration(parameters)
    config.configuration.update(branch_config)
    config.configuration.run_configuration.update({'input_draw_number': parameters['input_draw'],
                                                   'run_id': str(job_id) + '_' + str(time())})
    if branch_config is not None:
        run_key = dict(branch_config)
        run_key.update({'input_draw': parameters['input_draw'], 'random_seed': parameters['random_seed']})
        config.configuration.run_configuration.update({'run_key': run_key,
                                                       'results_directory': parameters['results_path']},
                                                      layer='override', source=str(job_id))
    logging.info('Simulation input config:')
    logging.info(str(config))

    component_manager = load_component_manager(config)
    simulation = setup_simulation(component_manager, config)
    return run(simulation)


def _format_output(parameters, metrics, final_state):
    from vivarium.framework.util import collapse_nested_dict

    idx = pd.MultiIndex.from_tuples([(input_draw, random_seed)],
                                    names=['input_draw_number', 'random_seed'])
    output = [pd.DataFrame(metrics, index=idx).to_json()]
    if with_state_table:
        final_state['input_draw_number'] = input_draw
        final_state['random_seed'] = random_seed
        if branch_config:
            for k, v in collapse_nested_dict(branch_config):
                final_state[k] = v
        output.append(final_state.to_json())
    return output
