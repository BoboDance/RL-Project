from abc import abstractmethod, ABC
import autograd.numpy as np


class Controller(ABC):

    @abstractmethod
    def choose_action(self, mu: np.ndarray, sigma: np.ndarray, bound: np.ndarray = None) -> tuple:
        pass

    @abstractmethod
    def set_params(self, params: np.ndarray):
        pass

    @abstractmethod
    def get_params(self):
        pass