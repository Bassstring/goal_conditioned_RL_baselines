from pyrep.pyrep import PyRep
from pyrep.robots.arms.panda import Panda
from pyrep.objects.shape import Shape
import numpy as np
from os.path import dirname, join, abspath
import gym
from gym import spaces
from gym.utils import seeding

SCENE_FILE = join(dirname(abspath(__file__)),
                  'scene_reinforcement_learning_env.ttt')
POS_MIN, POS_MAX = [0.8, -0.2, 1.0], [1.0, 0.2, 1.4]
EPISODES = 5
EPISODE_LENGTH = 200

class ReacherEnv(gym.GoalEnv):

    def __init__(self, headless=0, tmp=False):
        print('\033[92m' + 'Creating new Env' + '\033[0m')
        headless = bool(headless)

        # PyRep initialization
        self.pr = PyRep()
        self.pr.launch(SCENE_FILE, headless=headless)
        self.pr.start()

        # Load robot and set position
        self.agent = Panda()
        self.agent.set_control_loop_enabled(False)
        self.agent.set_motor_locked_at_zero_velocity(True)
        self.target = Shape('target')
        self.agent_ee_tip = self.agent.get_tip()
        self.initial_joint_positions = self.agent.get_joint_positions()

        # define goal
        self.goal = self._sample_goal()

        # set action space
        self.action_space = spaces.Box(-1., 1., shape=(7,), dtype='float32')
        obs = self._get_obs()
        self.observation_space = spaces.Dict(dict(
            desired_goal=spaces.Box(-np.inf, np.inf, shape=obs['achieved_goal'].shape, dtype='float32'),
            achieved_goal=spaces.Box(-np.inf, np.inf, shape=obs['achieved_goal'].shape, dtype='float32'),
            observation=spaces.Box(-np.inf, np.inf, shape=obs['observation'].shape, dtype='float32'),))

        # set if environment is only for short usage
        self.tmp = tmp

    def seed(self, seed=None):
        self.np_random, seed = seeding.np_random(seed)
        return [seed]

    def _get_obs(self):
        # Return state containing arm joint angles/velocities & target position
        obs = np.concatenate([self.agent.get_joint_positions(),
                              self.agent.get_joint_velocities()])
        achieved_goal = self.agent_ee_tip.get_position()
        obs = {'observation': obs.copy(), 'achieved_goal': achieved_goal.copy(),
               'desired_goal': np.array(self.goal.copy()),
               'non_noisy_obs': obs.copy()}
        return obs

    def _sample_goal(self):
        # Get a random position within a cuboid and set the target position
        pos = list(np.random.uniform(POS_MIN, POS_MAX))
        self.target.set_position(pos)
        return pos

    def reset(self):
        self.agent.set_joint_positions(self.initial_joint_positions)
        self.goal = self._sample_goal()
        obs = self._get_obs()
        if self.tmp > 0:
            self.tmp -= 1
            print('\033[91m' + 'This Env will shut down after ' + str(self.tmp) + ' resets' + '\033[0m')
            if self.tmp == 0:
                self.close()
                print('!'*200)
        return obs

    def compute_reward(self, achieved_goal, goal, info):
        if achieved_goal.shape[0] != 3:
            reward = [self.compute_reward(g1, g2, info) for g1, g2 in zip(achieved_goal, goal)]
        else:
            ax, ay, az = achieved_goal  # self.agent_ee_tip.get_position()
            tx, ty, tz = goal  # self.target.get_position()
            # Reward is negative distance to target
            reward = -np.sqrt((ax - tx) ** 2 + (ay - ty) ** 2 + (az - tz) ** 2)
        return np.array(reward)

    def _set_action(self, action):
        self.agent.set_joint_target_velocities(action)  # Execute action on arm

    def _is_success(self, achieved_goal, desired_goal):
        return self.compute_reward(achieved_goal, desired_goal, {}) > -0.05

    def step(self, action):
        self._set_action(action)
        self.pr.step()  # Step the physics simulation
        done = False
        obs = self._get_obs()
        is_success = self._is_success(obs['achieved_goal'], obs['desired_goal'])
        info = {'is_success': is_success}

        r = self.compute_reward(obs['achieved_goal'], obs['desired_goal'], {})
        return obs, r, done, info

    def close(self):
        print('\033[91m' + 'Closing Env' + '\033[0m')
        self.pr.stop()
        self.pr.shutdown()
