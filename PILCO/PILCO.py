import gym
import numpy as np
import quanser_robots
from numpy.linalg import solve
from scipy.optimize import minimize
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel

from PILCO.Controller import Controller
from PILCO.Controller.RBFController import RBFController
from PILCO.MGPR import MGPR


class PILCO(object):

    def __init__(self, env_name, seed, n_features, T):
        """

        :param env_name: gym env to work with
        :param seed: random seed for reproduceability
        :param n_features: Amount of features for RBF Controller
        :param T: number of steps for trajectory rollout, also defined as horizon
        """

        # -----------------------------------------------------
        # general
        self.env_name = env_name
        self.seed = seed
        self.n_features = n_features
        self.noise_var = None

        # -----------------------------------------------------
        # env setup
        # check if the requested environment is a quanser robot env
        if self.env_name in ['CartpoleStabShort-v0']:
            self.env = quanser_robots.GentlyTerminating(gym.make(self.env_name))
        else:
            # use the official gym env as default
            self.env = gym.make(self.env_name)
        self.env.seed(self.seed)

        # get the number of available action from the environment
        self.state_dim = self.env.observation_space.shape[0]
        self.n_actions = self.env.action_space.shape[0]

        # -----------------------------------------------------
        # dynamics model
        self.dynamics_model = None

        # -----------------------------------------------------
        # Hyperparameter to optimize
        # TODO change to meaning full inits
        self.l = np.random.normal(0, np.ones(self.state_dim))
        self.var_f = 1
        self.var_eps = 0.1  # target noise for prediction of GP and Policy

        # -----------------------------------------------------
        # policy search
        self.T = T

        # -----------------------------------------------------
        # Value calc and policy optimization
        # The cost function has to be learn with a GP as it comes from the environment and is not known.
        # TODO use GP for the cost function
        self.ridge = 1e-6
        # TODO What is the input to the cost gp, only state or state-action?
        # I think only state
        gp_length_scale = np.ones(self.state_dim)
        self.cost_model = GaussianProcessRegressor(kernel=RBF(length_scale=gp_length_scale) + WhiteKernel(self.ridge),
                                                   optimizer='fmin_l_bfgs_b')

        # -----------------------------------------------------
        # Container for collected experience
        self.states = []
        self.actions = []

    def run(self, n_init):

        # TODO maybe change the structure to PHD, page 36

        # sample dataset with random actions
        X = np.zeros((n_init, self.state_dim + self.n_actions))
        y = np.zeros((n_init, self.state_dim))
        rewards = np.zeros((n_init,))

        i = 0
        while i < n_init:
            state_prev = self.env.reset()
            done = False

            while not done and i < n_init:
                action = self.env.action_space.sample()
                state, reward, done, _ = self.env.step(action)

                state = np.array(state)
                state_prev = np.array(state_prev)

                # state-action pair as input
                X[i] = np.concatenate([state_prev, action])

                # delta to following state as output plus some noise
                y[i] = state - state_prev + np.random.multivariate_normal(np.ones(state.shape),
                                                                          self.var_eps * np.identity(state.shape[0]))

                rewards[i] = reward
                state_prev = state
                i += 1
                self.update_hyperparams(X, y, i)

        # convert to numpy
        X = np.array(X)
        y = np.array(y)
        rewards = np.array(rewards)

        # init model params

        # dimension of state vector
        # D = self.env.observation_space.shape[0]
        # dimension of action vector
        # F = self.env.action_space
        # W = np.random.normal(0, np.ones(self.n_features), size=(D, self.n_features))
        # sigma = np.random.normal(0, np.identity(self.state_dim))
        # mu = np.random.normal(0, 1, self.n_features)

        # create controller/policy
        # policy_sigma = np.random.normal(0, self.state_dim)
        # sigma = self.var_eps??

        # TODO change the ls, they are different to the ones from the gp and should be optimized
        policy = RBFController(X[:, :self.state_dim], self.var_eps, self.l[:self.state_dim])
        policy.update_params(X[:, :self.state_dim])

        while True:
            convergence = False
            # TODO:
            # We probably have to replace the sklearn GP with custom GP
            # to incorporate the uncertainty from p(x,u) into p(delta_x)
            self.learn_dynamics_model(X, y)
            tau_mus, tau_covs = self.rollout(policy, X)

            while True:
                # TODO: Evaluation works only if cost function is known. Employ GP to to represent cost function.
                # PHD Thesis, page 47, 3.5.4
                self.evaluate_policy()
                self.policy_improvement()
                W, sigma, mu = self.update_params(W, sigma, mu)

                if convergence:
                    break

            policy.update_params(W, sigma, mu)
            X_test, y_test, reward_test = self.execute_test_run(policy)
            np.append(X, X_test)
            np.append(y, y_test)
            np.append(rewards, reward_test)

            # for debugging
            break

    def learn_dynamics_model(self, X, y):
        self.update_hyperparams(X, y)
        self.dynamics_model = MGPR(dim=self.env.observation_space.shape[0], length_scale=self.l, sigma_f=self.var_f,
                                   sigma_eps=self.var_eps)
        self.dynamics_model.fit(X, y)

    def evaluate_policy(self):
        # TODO
        # Compute mean and covar of policy/control dist
        # Compute cross covar[x-1, u-1]
        # approx state control dist p(\tildex-1) = p(x-1, u-1) = N(x\tilde-1|mu-1,sigma-1)
        #

        raise NotImplementedError

    def policy_improvement(self):
        # TODO: optimize hyperparams theta of policy
        # requires function to be optimized as well as optional gradient
        # args = (2, 3, 7, 8, 9, 10)  # parameter values
        #
        # def f(x, *args):
        #     u, v = x
        #     a, b, c, d, e, f = args
        #     return a * u ** 2 + b * u * v + c * v ** 2 + d * u + e * v + f
        #
        # def gradf(x, *args):
        #     u, v = x
        #     a, b, c, d, e, f = args
        #     gu = 2 * a * u + b * v + d  # u-component of the gradient
        #     gv = b * u + 2 * c * v + e  # v-component of the gradient
        #     return np.asarray((gu, gv))
        #
        # x0 = np.asarray((0, 0))  # Initial guess.

        # https: // docs.scipy.org / doc / scipy / reference / generated / scipy.optimize.fmin_cg.html  # scipy-optimize-fmin-cg
        # ---------------------------------
        # Example 2: solve the same problem using the minimize function. (This myopts dictionary shows all of the
        # available options, although in practice only non-default values would be needed.
        # The returned value will be a dictionary.)
        # >>> opts = {'maxiter' : None,    # default value.
        # ...         'disp' : True,    # non-default value.
        # ...         'gtol' : 1e-5,    # default value.
        # ...         'norm' : np.inf,  # default value.
        # ...         'eps' : 1.4901161193847656e-08}  # default value.
        # >>> res2 = optimize.minimize(f, x0, jac=gradf, args=args,
        # ...                          method='CG', options=opts)
        # Optimization terminated successfully.
        #         Current function value: 1.617021
        #         Iterations: 4
        #         Function evaluations: 8
        #         Gradient evaluations: 8
        # >>> res2.x  # minimum found
        # array([-1.80851064, -0.25531915])

        # ---------------------------------

        minimize()
        raise NotImplementedError

    def update_params(self, W, sigma, mu):
        # use CG or L-BFGS for updates
        # TODO
        return W, sigma, mu

    def execute_test_run(self, policy):
        X = []
        y = []
        rewards = []

        state_prev = self.env.reset()
        done = False

        while not done:
            # TODO
            action = policy.predict(state_prev)
            state, reward, done, _ = self.env.step(action)

            # create history and create new training instance
            X.append(np.append(state_prev, action))
            epsilon = np.random.normal(0, self.noise_var)
            y.append(state - state_prev + epsilon)

            state_prev = state

        return X, y, reward

    def update_hyperparams(self, X, y, i=None):
        """
        Compute hyperparams for GPR
        :param X: training vector containing values for [x,u]^T
        :param y: target vector containing deltas of states
        :return:
        """
        self.l = np.var(X[:i, :], axis=0)
        self.var_f = np.var(y[:i, :])
        self.var_eps = np.var(y[:i, :] / 10)

    def rollout(self, policy: Controller, X):

        # TODO get the mu of initial state x_t here
        # state_mu = X[:, :self.state_dim].mean(axis=0)
        state_mu = np.zeros((self.state_dim,))
        state_tilde_mu = X.mean(axis=0)  # TODO: section 3-3 in Master thesis

        # TODO get the cov of initial state x_t here
        # state_cov = X[:, :self.state_dim].std(axis=0)
        # state_cov = np.cov(X[:, :self.state_dim], rowvar=False
        state_cov = np.zeros((self.state_dim, self.state_dim))
        state_tilde_cov = np.cov(X, rowvar=False)  # TODO: section 3-3 in Master thesis

        # container
        trajectory_mu = np.zeros((self.T + 1, state_mu.shape[0]))
        trajectory_cov = np.zeros((self.T + 1, state_cov.shape[0], state_cov.shape[1]))

        trajectory_mu[0] = state_mu
        trajectory_cov[0] = state_cov

        for t in range(1, self.T + 1):
            # get mean over next action
            # TODO: Make this viable for x~N(mu, sigma)
            # PHD, page 44, Nonlinear Model: RBF Network
            # This is only correct for the point case, but not a dist
            prediction_mu = policy.predict(state_mu.reshape(1, -1))[0]

            # ------------------------------------------------
            # TODO: This might be wrong, see PHD thesis page 46, 2a)+b) and Section 2.3.2
            # Needs to be
            # squash and scale prediction
            prediction_mu = self.env.action_space.high * np.sin(prediction_mu)

            # get dist over successor state
            p_xu = np.concatenate([state_mu, prediction_mu])
            # ------------------------------------------------

            delta_mu, delta_cov = self.dynamics_model.predict(p_xu.reshape(1, -1), return_cov=True)
            delta_mu = delta_mu[0]

            # compute mean and cov of successor state dist
            # # compute precision matrix and different inv
            precision = np.diag(self.l)
            # state_tilde_cov_inv = solve(state_tilde_cov, np.identity(len(state_tilde_cov)))
            # precision_inv = solve(precision, np.identity(len(precision)))
            # precision_absolute_inv = solve(np.abs(precision), np.identity(len(precision)))
            #
            # exp = -(self.state_dim + self.n_actions) * .5
            #
            # c1_inv = (1 / self.var_f) * (2 * np.pi) ** exp * precision_absolute_inv ** .5
            #
            # const = (2 * np.pi) ** exp
            # # diff = (X - state_tilde_mu)
            # inv = solve(precision + state_tilde_cov, np.identity(len(precision)))
            # c2_inv = np.array([const * np.exp(-.5 * (x - state_tilde_mu).T @ inv @ (x - state_tilde_mu)) for x in X])
            #
            # omega = solve(precision_inv + state_tilde_cov_inv, np.identity(len(precision_inv)))
            # w = (X @ precision_inv.T + state_tilde_cov_inv @ state_tilde_mu) @ omega
            #
            # q = np.array([np.linalg.inv(c1_inv) * c for c in c2_inv])
            # beta = delta_mu @ np.linalg.inv(q)
            #
            # state_tilde_delta_mu = np.sum((1 / c1_inv) * c2_inv) * beta * w

            betas = self.dynamics_model.get_alphas()
            qs = np.array([betas[i] / mu for i, mu in enumerate(delta_mu)])

            matrices = state_tilde_cov @ solve(state_tilde_cov + precision, np.identity(state_tilde_cov.shape[0])) @ (
                    X - state_tilde_mu).T

            cross_cov_state_tilde = np.array([np.sum(betas[i] * qs[i] * matrices, axis=1) for i in range(len(betas))])
            cross_cov_state = cross_cov_state_tilde[:, :self.state_dim]

            state_next_mu = state_mu + delta_mu
            state_next_cov = state_cov + delta_cov + cross_cov_state + cross_cov_state

            trajectory_mu[t] = state_next_mu
            trajectory_cov[t] = state_next_cov

            state_cov = state_next_cov
            state_mu = state_next_mu

            # ------------------------------------------------
            # This should be the output of the GP model
            # ------------------------------------------------
            # for e in range(y.shape[1]):
            #     # TODO: K is probably the eth kernel value
            #     # K = None
            #
            #     betas[e] = np.linalg.solve(K[e] + self.sigma_eps[e] * np.identity(K[e].shape[0]),
            #                                np.identity(K[e].shape[0])) @ y
            #
            #     if isinstance(self.l[e], float):
            #         temp = state_tilde_cov / self.l[e]
            #     else:
            #         temp = state_tilde_cov * np.linalg.solve(self.l[e], np.identity(self.l[e].shape))
            #
            #     diff = X - state_tilde_mu
            #
            #     # TODO: Finish with pseudo-algo on master thesis
            #     # q[e] = self.sigma_f[e] * np.abs(temp + np.identity(temp.shape[0])) ** .5 * np.exp(-.5 (p_xu - ))

        return trajectory_mu, trajectory_cov

    # def compute_deltas(self, x: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    #     """
    #     Returns the deltas between the states plus some noise
    #     :param x: sequence of recorded states
    #     :param sigma: diagonal matrix containing the std for each state index
    #     """
    #     deltas = []
    #
    #     for i, x1 in enumerate(x):
    #         epsilon = np.random.normal(0, sigma)
    #         if i == 0:
    #             deltas.append(x1 + epsilon)
    #         deltas.append(x1 - x[i - 1] + epsilon)
    #
    #     return deltas

    # def rollout(self, start, policy, episode_len, plant, cost):
    #     """
    #     # from: pilco-matlab - https://github.com/ICL-SML/pilco-matlab
    #
    #     Run multiple rollouts on a trajectory until you reach a terminal state.
    #
    #     % 1. Generate trajectory rollout given the current policy
    #
    #     if isfield(plant,'constraint'), HH = maxH; else HH = H; end
    #
    #     # 1. Generate a trajectory rollout by applying the current policy to the system
    #     #  The initial state is sampled from p(x0 ) = N (mu0, S0)
    #     [xx, yy, realCost{j+J}, latent{j}] = ...
    #       rollout(gaussian(mu0, S0), policy, HH, plant, cost);
    #
    #
    #     disp(xx);                           % display states of observed trajectory
    #
    #     x = [x; xx]; y = [y; yy];                            % augment training set
    #
    #     if plotting.verbosity > 0
    #       if ~ishandle(3); figure(3); else set(0,'CurrentFigure',3); end
    #       hold on; plot(1:length(realCost{J+j}),realCost{J+j},'r'); drawnow;
    #     end
    #
    #
    #     function [x y L latent] = rollout(start, policy, H, plant, cost)
    #     %% Code
    #     % augi indicies for variables augmented to the ode variables
    #
    #     if isfield(plant,'augment'), augi = plant.augi;             % sort out indices!
    #     else plant.augment = inline('[]'); augi = []; end
    #
    #     if isfield(plant,'subplant'), subi = plant.subi;
    #     else plant.subplant = inline('[]',1); subi = []; end
    #
    #     odei = plant.odei; poli = plant.poli; dyno = plant.dyno; angi = plant.angi;
    #     simi = sort([odei subi]);
    #     nX = length(simi)+length(augi); nU = length(policy.maxU); nA = length(angi);
    #
    #     state(simi) = start; state(augi) = plant.augment(state);      % initializations
    #     x = zeros(H+1, nX+2*nA);
    #     x(1,simi) = start' + randn(size(simi))*chol(plant.noise);
    #     x(1,augi) = plant.augment(x(1,:));
    #     u = zeros(H, nU); latent = zeros(H+1, size(state,2)+nU);
    #     y = zeros(H, nX); L = zeros(1, H); next = zeros(1,length(simi));
    #
    #     for i = 1:H % --------------------------------------------- generate trajectory
    #       s = x(i,dyno)'; sa = gTrig(s, zeros(length(s)), angi); s = [s; sa];
    #       x(i,end-2*nA+1:end) = s(end-2*nA+1:end);
    #
    #       % 1. Apply policy ... or random actions --------------------------------------
    #       if isfield(policy, 'fcn')
    #         u(i,:) = policy.fcn(policy,s(poli),zeros(length(poli)));
    #       else
    #         u(i,:) = policy.maxU.*(2*rand(1,nU)-1);
    #       end
    #       latent(i,:) = [state u(i,:)];                                  % latent state
    #
    #     :return:
    #     """
    #
    #     # check for system constraint boundaries
    #     high = np.asscalar(self.env.action_space.high)
    #     low = np.asscalar(self.env.action_space.low)
    #
    #     # For our gym-environments we don't need the ODE because we already get sufficient env-feedback
    #     # idces_ode_solver = None
    #     # idces_agugmenting_ode = None
    #     # idces_dynamics_out = None
    #     # ...
    #
    #     # variable
    #     x = np.zeros([episode_len + 1, self.state_dim + 2 * self.n_actions])
    #     # x[0, odei] = multivariate_normal(start, plant.noise)
    #
    #     actions_u = np.zeros((episode_len, self.n_actions))
    #     targets_y = np.zeros((episode_len, self.state_dim))
    #     loss_l = np.zeros(episode_len)
    #     latent = np.zeros((episode_len + 1, self.n_features + self.state_dim))
    #
    #     # run multiple rollouts along a trajectory for episode_len number of steps.
    #
    #     # inital state dist
    #     p_x = None
    #
    #     for i in range(episode_len):
    #         # # dist over actions from policy
    #         # p_u = policy.predict(p_x)
    #         #
    #         # # dist over state delta given current state and action dist
    #         # p_x_u = p_x + p_u
    #         # p_delta = self.mgp.predict(p_x_u)
    #         #
    #         # # get state dist for x_t+1
    #         # # p_x = TODO
    #
    #         raise NotImplementedError
    #
    #     return x, targets_y, loss_l, latent
