import click
import importlib

_global_options = [
click.option('--env', type=str, default='NicobotTowerBuildMujocoEnv-sparse-gripper_random-o3-h1-3-v1', help='the name of the OpenAI Gym environment that you want to train on'),
click.option('--algorithm', default='baselines.her', help='the name of the algorithm to be used',
             type=click.Choice(['baselines.her', 'baselines.model_based', 'baselines.her_pddl', 'baselines.herhrl'])
             ),
click.option('--base_logdir', type=str, default='/data/eppe/her_pddl_data', help='the path to where logs and policy pickles should go. If not specified, creates a folder in /tmp/'),
click.option('--n_epochs', type=int, default=300, help='the max. number of training epochs to run'),
click.option('--num_cpu', type=int, default=1, help='the number of CPU cores to use (using MPI)'),
click.option('--seed', type=int, default=0, help='the random seed used to seed both the environment and the training code'),
click.option('--policy_save_interval', type=int, default=50, help='the interval with which policy pickles are saved. If set to 0, only the best and latest policy will be pickled.'),
click.option('--restore_policy', type=str, default=None, help='The pretrained policy file to start with to avoid learning from scratch again. Useful for interrupting and restoring training sessions.'),
click.option('--rollout_batch_size', type=int, default=2, help='The number of simultaneous rollouts.'),
click.option('--n_episodes', type=int, default=100, help='The number of training episodes (parallel rollouts) per epoch.'),
click.option('--render', type=int, default=1, help='Whether or not to render the rollout execution.'),
click.option('--max_try_idx', type=int, default=199, help='Max. number of tries for this training config.'),
click.option('--try_start_idx', type=int, default=100, help='Index for first try.'),
click.option('--early_stop_success_rate', type=int, default=95, help='The required mean success rate  over the last 4 epochs in % to trigger early stopping. 0 for no early stopping'),
click.option('--info', type=str, default='', help='A command line comment that will be integrated in the folder where the results are stored. Useful for debugging and addressing temporary changes to the code..')
]

@click.command(context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
))
@click.pass_context
def get_policy_click(ctx, **kwargs):
    policy_linker = importlib.import_module(kwargs['algorithm'] + ".interface.click_options", package=__package__)
    policy_args = ctx.forward(policy_linker.get_click_option)
    return policy_args

def import_creator(library_path):
    config = importlib.import_module(library_path + ".interface.config", package=__package__)
    RolloutWorker = getattr(importlib.import_module(library_path + ".rollout", package=__package__), "RolloutWorker")
    # policy_linker = importlib.import_module(library_path + ".interface.click_options", package=__package__)
    # from baselines.her.experiment.plot import load_results
    return config, RolloutWorker


def click_main(func):
    for option in reversed(_global_options):
        func = option(func)
    return func