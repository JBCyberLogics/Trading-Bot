"""Genetic algorithm for evolutionary parameter optimization."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple
import random

from loguru import logger
import numpy as np


class GeneticOptimizer:
    """Genetic search for improved parameter combinations."""

    def __init__(
        self,
        db,
        parameter_ranges: Dict[str, Dict[str, float]],
        population_size: int = 50,
        generations: int = 20,
    ) -> None:
        self.db = db
        self.parameter_ranges = parameter_ranges
        self.population_size = int(population_size)
        self.generations = int(generations)

        self.mutation_rate = 0.10
        self.crossover_rate = 0.70
        self.elite_size = max(1, int(self.population_size * 0.10))

        logger.info(f"GeneticOptimizer initialized: pop={self.population_size}, gens={self.generations}")

    def random_individual(self) -> Dict[str, float]:
        """Generate random parameter set within configured ranges."""
        individual: Dict[str, float] = {}
        for param, range_info in self.parameter_ranges.items():
            min_val = float(range_info["min"])
            max_val = float(range_info["max"])
            step = float(range_info.get("step", 0.0))

            if step > 0:
                steps = int((max_val - min_val) / step)
                idx = random.randint(0, max(steps, 0))
                value = min_val + idx * step
            else:
                value = random.uniform(min_val, max_val)
            individual[param] = float(value)
        return individual

    def initialize_population(self) -> List[Dict[str, float]]:
        """Create initial random population."""
        return [self.random_individual() for _ in range(self.population_size)]

    def fitness(self, individual: Dict[str, Any]) -> float:
        """Score an individual. Higher score is better."""
        if not self.parameter_ranges:
            return 0.0

        scores: List[float] = []
        for param, range_info in self.parameter_ranges.items():
            value = individual.get(param)
            if not isinstance(value, (int, float)):
                continue

            min_val = float(range_info["min"])
            max_val = float(range_info["max"])
            if max_val <= min_val:
                continue

            norm = (float(value) - min_val) / (max_val - min_val)
            norm = max(0.0, min(1.0, norm))

            # Mildly favor center-biased exploration for stability.
            score = 1.0 - abs(norm - 0.5)
            scores.append(score)

        if not scores:
            return 0.0
        return float(np.mean(scores))

    def evaluate_population(self, population: List[Dict[str, float]]) -> List[float]:
        """Evaluate fitness for entire population."""
        return [self.fitness(individual) for individual in population]

    def select_parents(
        self,
        population: List[Dict[str, float]],
        fitness_scores: List[float],
        num_parents: int,
    ) -> List[Dict[str, float]]:
        """Tournament selection."""
        parents: List[Dict[str, float]] = []
        tournament_size = 3

        for _ in range(num_parents):
            indices = random.sample(range(len(population)), min(tournament_size, len(population)))
            best_index = max(indices, key=lambda idx: fitness_scores[idx])
            parents.append(copy.deepcopy(population[best_index]))

        return parents

    def crossover(self, parent1: Dict[str, float], parent2: Dict[str, float]) -> Dict[str, float]:
        """Blend crossover for real-valued parameters."""
        child: Dict[str, float] = {}

        for param in parent1:
            if random.random() < self.crossover_rate:
                alpha = random.uniform(0.3, 0.7)
                value = alpha * parent1[param] + (1 - alpha) * parent2[param]
            else:
                value = parent1[param] if random.random() < 0.5 else parent2[param]

            range_info = self.parameter_ranges[param]
            value = max(float(range_info["min"]), min(float(range_info["max"]), float(value)))
            child[param] = value

        return child

    def mutate(self, individual: Dict[str, float]) -> Dict[str, float]:
        """Mutate individual with bounded random shifts."""
        mutated = copy.deepcopy(individual)

        for param in mutated:
            if random.random() >= self.mutation_rate:
                continue

            range_info = self.parameter_ranges[param]
            min_val = float(range_info["min"])
            max_val = float(range_info["max"])
            step = float(range_info.get("step", 0.0))

            max_shift = (max_val - min_val) * 0.10
            shift = random.uniform(-max_shift, max_shift)
            new_value = float(mutated[param]) + shift

            if step > 0:
                steps = round((new_value - min_val) / step)
                new_value = min_val + steps * step

            mutated[param] = max(min_val, min(max_val, new_value))

        return mutated

    def evolve(self) -> Tuple[Dict[str, float], float]:
        """Run genetic evolution and return best individual and score."""
        population = self.initialize_population()

        for generation in range(self.generations):
            fitness_scores = self.evaluate_population(population)
            sorted_indices = np.argsort(fitness_scores)[::-1]
            elites = [copy.deepcopy(population[i]) for i in sorted_indices[: self.elite_size]]

            num_parents = self.population_size - len(elites)
            parents = self.select_parents(population, fitness_scores, max(num_parents, 1))

            next_generation = elites.copy()
            while len(next_generation) < self.population_size:
                parent1 = random.choice(parents)
                parent2 = random.choice(parents)
                child = self.crossover(parent1, parent2)
                child = self.mutate(child)
                next_generation.append(child)

            population = next_generation
            logger.info(f"Generation {generation + 1}: best fitness = {max(fitness_scores):.4f}")

        final_fitness = self.evaluate_population(population)
        best_idx = int(np.argmax(final_fitness))
        return population[best_idx], float(final_fitness[best_idx])
