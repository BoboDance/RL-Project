import copy
import logging
import time
from typing import Union

import gym
import numpy as np
import quanser_robots
import torch
from baselines.common.vec_env.dummy_vec_env import DummyVecEnv
from baselines.common.vec_env.subproc_vec_env import SubprocVecEnv
from gym.wrappers.monitor import Monitor
from tensorboardX import SummaryWriter
from torch.multiprocessing import Value

from a3c.models.actor_critic_network import ActorCriticNetwork
from a3c.models.actor_network import ActorNetwork
from a3c.models.critic_network import CriticNetwork
from a3c.util.util import get_normalizer, make_env, sync_grads, log_to_tensorboard, get_optimizer, shape_reward
from a3c.util.util import save_checkpoint


def test(args, worker_id: int, global_model: torch.nn.Module, T: Value, global_reward: Value = None,
         optimizer: torch.optim.Optimizer = None, global_model_critic: CriticNetwork = None,
         optimizer_critic: torch.optim.Optimizer = None):
    """
    Start worker in _test mode, i.e. no training is done, only testing is used to validate current performance
    loosely based on https://github.com/ikostrikov/pytorch-a3c/blob/master/_test.py
    :param args: console arguments
    :param worker_id: id of worker to differentiatethem and init different seeds
    :param global_model: global model, which is optimized/ for split models: actor
    :param T: global counter of steps
    :param global_reward: global running reward value
    :param optimizer: optimizer for shared model/ for split models: actor model
    :param global_model_critic: optional global critic model for split networks
    :param optimizer_critic: optional critic optimizer for split networks
    :return: None
    """

    logging.info("test worker started.")
    torch.manual_seed(args.seed + worker_id)

    if "RR" in args.env_name:
        env = quanser_robots.GentlyTerminating(gym.make(args.env_name))
    else:
        if args.monitor:
            env = Monitor(gym.make(args.env_name), '100_test_runs', video_callable=lambda count: count % 100 == 0,
                          force=True)
        else:
            env = gym.make(args.env_name)

    env.seed(args.seed + worker_id)

    normalizer = get_normalizer(args.normalizer, env)

    # get an instance of the current global model state
    model = copy.deepcopy(global_model)
    model.eval()

    model_critic = None
    if global_model_critic:
        model_critic = copy.deepcopy(global_model_critic)
        model_critic.eval()

    state = torch.from_numpy(env.reset())

    writer = SummaryWriter(comment='_test', log_dir='experiments/runs/')
    start_time = time.time()

    t = 0
    episode_reward = 0

    done = False
    global_iter = 0
    best_global_reward = -np.inf
    best_test_reward = -np.inf

    while True:

        # Get params from shared global model
        model.load_state_dict(global_model.state_dict())
        if not args.shared_model:
            model_critic.load_state_dict(global_model_critic.state_dict())

        rewards = []
        eps_len = []

        sleep = True

        # make 10 runs to get current avg performance
        for i in range(args.test_runs):
            while not done:
                t += 1

                if not args.no_render:
                    if i == 0 and t % 1 == 0 and "RR" not in args.env_name:
                        env.render()
                        if args.monitor and sleep:  # add a small delay to do a screen capture of the test run if needed
                            time.sleep(1)
                            sleep = False

                # apply min/max scaling on the environment

                with torch.no_grad():

                    # select mean of normal dist as action --> Expectation
                    if args.shared_model:
                        _, mu, _ = model(normalizer(state))
                    else:
                        mu, _ = model(normalizer(state))

                    action = mu.detach()

                state, reward, done, _ = env.step(np.clip(action.numpy(), -args.max_action, args.max_action))

                done = done or t >= args.max_episode_length
                episode_reward += reward

                if done:
                    # reset current cumulated reward and episode counter as well as env
                    rewards.append(episode_reward)
                    episode_reward = 0

                    eps_len.append(t)
                    t = 0

                    state = env.reset()

                state = torch.from_numpy(state)

            # necessary to make more than one run
            done = False

        time_print = time.strftime("%Hh %Mm %Ss", time.gmtime(time.time() - start_time))

        std_reward = np.std(rewards)
        rewards = np.mean(rewards)

        new_best = rewards > best_test_reward
        writer.add_scalar("reward/test", rewards, int(T.value))
        writer.add_scalar("episode/length", np.mean(eps_len), int(T.value))

        log_string = f"Time: {time_print}, T={T.value} -- n_runs={args.test_runs} -- mean total reward={rewards:.5f} " \
            f" +/- {std_reward:.5f} -- mean episode length={np.mean(eps_len):.5f}" \
            f" +/- {np.std(eps_len):.5f} -- global reward={global_reward.value:.5f}"

        if new_best:
            # highlight messages if progress was done
            logging.info(log_string)

            best_global_reward = global_reward.value if global_reward.value > best_global_reward else best_global_reward
            best_test_reward = rewards if rewards > best_test_reward else best_test_reward
            model_type = 'shared' if args.shared_model else 'split'

            save_checkpoint({
                'epoch': T.value,
                'model': model.state_dict(),
                'model_critic': model_critic.state_dict() if model_critic is not None else None,
                'global_reward': global_reward.value,
                # only save optimizers if shared ones are used
                'optimizer': optimizer.state_dict() if optimizer else None,
                'optimizer_critic': optimizer_critic.state_dict() if optimizer_critic else None,
            },
                path=f"./experiments/checkpoints/model_{model_type}_T-{T.value}_global-{global_reward.value:.5f}_test-{rewards:.5f}.pth.tar")
        else:
            # use by default only debug messages if no progress was reached
            logging.debug(log_string)

        global_iter += 1

        # run evaluation only once in test mode
        if args.test:
            break


def train(args, worker_id: int, global_model: Union[ActorNetwork, ActorCriticNetwork], T: Value, global_reward: Value,
          optimizer: torch.optim.Optimizer = None, global_model_critic: CriticNetwork = None,
          optimizer_critic: torch.optim.Optimizer = None, lr_scheduler: torch.optim.lr_scheduler = None,
          lr_scheduler_critic: torch.optim.lr_scheduler = None):
    """
    Start worker in training mode, i.e. training the shared model with backprop
    loosely based on https://github.com/ikostrikov/pytorch-a3c/blob/master/train.py
    :param args: console arguments
    :param worker_id: id of worker to differentiatethem and init different seeds
    :param global_model: global model, which is optimized/ for split models: actor
    :param T: global counter of steps
    :param global_reward: global running reward value
    :param optimizer: optimizer for shared model/ for split models: actor model
    :param global_model_critic: optional global critic model for split networks
    :param optimizer_critic: optional critic optimizer for split networks
    :param lr_scheduler: optional learning rate scheduler instance for shared model
    / for fixed model: actor learning rate scheduler
    :param lr_scheduler_critic: optional learning rate scheduler instance for critic model
    :return: None
    """
    torch.manual_seed(args.seed + worker_id)

    if args.worker == 1:
        logging.info(f"Running A2C with {args.n_envs} environments.")
        if "RR" not in args.env_name:
            env = SubprocVecEnv([make_env(args.env_name, args.seed, i, args.log_dir) for i in range(args.n_envs)])
        else:
            env = DummyVecEnv([make_env(args.env_name, args.seed, worker_id, args.log_dir)])
    else:
        logging.info(f"Running A3C: training worker {worker_id} started.")
        env = DummyVecEnv([make_env(args.env_name, args.seed, worker_id, args.log_dir)])
        # avoid any issues if this is not 1
        args.n_envs = 1

    normalizer = get_normalizer(args.normalizer, env)

    # init local NN instance for worker thread
    model = copy.deepcopy(global_model)
    model.train()

    model_critic = None

    if global_model_critic:
        model_critic = copy.deepcopy(global_model_critic)
        model_critic.train()

    # if no shared optimizer is provided use individual one
    if not optimizer:
        optimizer, optimizer_critic = get_optimizer(args.optimizer, global_model, args.lr,
                                                    model_critic=global_model_critic, lr_critic=args.lr_critic)
        if args.lr_scheduler == "exponential":
            lr_scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.99)
            if optimizer_critic:
                lr_scheduler_critic = torch.optim.lr_scheduler.ExponentialLR(optimizer_critic, gamma=0.99)

    state = torch.Tensor(env.reset())

    t = np.zeros(args.n_envs)
    global_iter = 0
    episode_reward = np.zeros(args.n_envs)

    if worker_id == 0:
        writer = SummaryWriter(log_dir='experiments/runs/')

    while True:
        # Get state of the global model
        model.load_state_dict(global_model.state_dict())
        if not args.shared_model:
            model_critic.load_state_dict(global_model_critic.state_dict())

        # containers for computing loss
        values = []
        log_probs = []
        rewards = []
        entropies = []
        # container to check whether a terminal state was reached from one of the envs
        terminals = []

        # reward_sum = 0
        for step in range(args.rollout_steps):
            t += 1

            if args.shared_model:
                value, mu, std = model(normalizer(state))
            else:
                mu, std = model(normalizer(state))
                value = model_critic(normalizer(state))

            dist = torch.distributions.Normal(mu, std)

            # ------------------------------------------
            # # select action
            action = dist.sample()

            # ------------------------------------------
            # Compute statistics for loss
            entropy = dist.entropy().sum(-1).unsqueeze(-1)
            log_prob = dist.log_prob(action).sum(-1).unsqueeze(-1)

            # make selected move
            action = np.clip(action.detach().numpy(), -args.max_action, args.max_action)
            state, reward, dones, _ = env.step(action[0] if not args.worker == 1 or "RR" in args.env_name else action)

            reward = shape_reward(args, reward)

            episode_reward += reward

            # probably don't set terminal state if max_episode length
            dones = np.logical_or(dones, t >= args.max_episode_length)

            values.append(value)
            log_probs.append(log_prob)
            rewards.append(torch.Tensor(reward).unsqueeze(-1))
            entropies.append(entropy)
            terminals.append(torch.Tensor(1 - dones).unsqueeze(-1))

            for i, done in enumerate(dones):
                if done:
                    # keep track of the avg overall global reward
                    with global_reward.get_lock():
                        if global_reward.value == -np.inf:
                            global_reward.value = episode_reward[i]
                        else:
                            global_reward.value = .99 * global_reward.value + .01 * episode_reward[i]
                    if worker_id == 0 and T.value % args.log_frequency == 0:
                        writer.add_scalar("reward/global", global_reward.value, T.value)

                    episode_reward[i] = 0
                    t[i] = 0
                    if args.worker != 1 or "RR" in args.env_name:
                        env.reset()

            with T.get_lock():
                # this is one for a3c and n for A2C (actually the lock is not needed for A2C)
                T.value += args.n_envs

            if lr_scheduler and worker_id == 0 and T.value % args.lr_scheduler_step and global_iter != 0:
                lr_scheduler.step(T.value / args.lr_scheduler_step)

                if lr_scheduler_critic:
                    lr_scheduler_critic.step(T.value / args.lr_scheduler_step)

            state = torch.Tensor(state)

        if args.shared_model:
            v, _, _ = model(normalizer(state))
            G = v.detach()
        else:
            G = model_critic(normalizer(state)).detach()

        values.append(G)

        # compute loss and backprop
        advantages = torch.zeros((args.n_envs, 1))

        ret = torch.zeros((args.rollout_steps, args.n_envs, 1))
        adv = torch.zeros((args.rollout_steps, args.n_envs, 1))

        # iterate over all time steps from most recent to the starting one
        for i in reversed(range(args.rollout_steps)):
            # G can be seen essentially as the return over the course of the rollout
            G = rewards[i] + args.discount * terminals[i] * G
            if not args.no_gae:
                # Generalized Advantage Estimation
                td_error = rewards[i] + args.discount * terminals[i] * values[i + 1] - values[i]
                # terminals here to "reset" advantages to 0, because reset ist called internally in the env
                # and new trajectory started
                advantages = advantages * args.discount * args.tau * terminals[i] + td_error
            else:
                advantages = G - values[i].detach()

            adv[i] = advantages.detach()
            ret[i] = G.detach()

        policy_loss = -(torch.stack(log_probs) * adv).mean()
        # minus 1 in order to remove the last element, which is only necessary for next timestep value
        value_loss = .5 * (ret - torch.stack(values[:-1])).pow(2).mean()
        entropy_loss = torch.stack(entropies).mean()

        # zero grads to reset the gradients
        optimizer.zero_grad()

        if args.shared_model:
            # combined loss for shared architecture
            total_loss = policy_loss + args.value_loss_weight * value_loss - args.entropy_loss_weight * entropy_loss
            total_loss.backward()
        else:
            optimizer_critic.zero_grad()

            value_loss.backward()
            (policy_loss - args.entropy_loss_weight * entropy_loss).backward()

            # this is just used for plotting in tensorboard
            total_loss = policy_loss + args.value_loss_weight * value_loss - args.entropy_loss_weight * entropy_loss

        torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)
        sync_grads(model, global_model)
        optimizer.step()

        if not args.shared_model:
            torch.nn.utils.clip_grad_norm_(model_critic.parameters(), args.max_grad_norm)
            sync_grads(model_critic, global_model_critic)
            optimizer_critic.step()

        global_iter += 1

        if worker_id == 0 and T.value % args.log_frequency == 0:
            log_to_tensorboard(writer, model, optimizer, rewards, values, total_loss, policy_loss, value_loss,
                               entropy_loss, T.value, model_critic=model_critic, optimizer_critic=optimizer_critic)
