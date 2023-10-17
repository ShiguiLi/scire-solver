# SciRE-Solver: Accelerating Diffusion Models Sampling  by Score-integrand Solver with Recursive Difference



Created by [Shigui Li](https://ShiguiLi.github.io/)\*, [Wei Chen](https://scholar.google.com/citations?hl=en&user=n5VpiAMAAAAJ), [Delu Zeng](https://scholar.google.com/citations?user=08RCdoIAAAAJ)

This code is an official demo of PyTorch implementation of SciRE-Solver.


[ArXiv](https://doi.org/10.48550/arXiv.2308.07896)

**SciRE-Solver is a category of accelerated sampling algorithms designed for diffusion models. It is founded on the following recursive difference method used to compute derivatives of the score function networks.**
<p align="center">
  <img src="./assets/Recursive_Difference.jpg" width="100%">
</p>


SciRE-Solver encompasses two algorithm types: **scire_v1** and **scire_v2**, which come with three available iteration modes: **multistep**, **singlestep_agile**, and **singlestep_fixed**. 

**SciRE-Solver, while accelerating, has achieved a better 'FID' compared to the previous achievements of pre-trained models.**





# [Stable-Diffusion](https://github.com/Stability-AI/StableDiffusion) 
The code is now available in the ['sd_scire'](sd_scire/stable-diffusion/ldm/models/diffusion/scire_solver) folder, and we welcome everyone to use the scire-solver on stable-diffusion. Next, we will integrate the code into the stable-diffusion repository.

Samples by Stable-Diffusion with SciRE-Solver and DPM-Solver++, using 50 NFE, and text prompt “A beautiful mansion beside a waterfall in the woods, by josef thoma, matte painting, trending on artstation HQ”, and seed 33. 
<p align="center">
  <img src="./assets/scirev12m3m_50.png" width="100%">
</p>
<p align="center"><strong>scire_v1 with 2m (left) and 3m (right).</strong> </p>
<p align="center">
  <img src="./assets/scirev22m3m_50.png" width="100%">
</p>
<p align="center"><strong>scire_v2 with 2m (left) and 3m (right).</strong> </p>
<p align="center">
  <img src="./assets/dpm++2m3m_50.png" width="100%">
</p>
<div style="text-align:center;">
  <strong><a href="https://arxiv.org/abs/2211.01095">DPM-Solver++</a> with 2m (left) and 3m (right).</strong>
</div>



Here we provide two versions of the 'scire-solver', and three different types of methods.
- algorithm_type: 'scire_v1' or 'scire_v2'
- method: "multistep" or "singlestep_agile" or "singlestep_fixed".

- When using a small number of sampling steps, we recommend using "multistep" method as:
  - "scire_v1-2m"
  - "scire_v1-3m"
  - "scire_v2-2m"
  - "scire_v2-3m" (at 6 steps, it is empirically better than other "m" options.)
- When the number of sampling steps '>= 26', we recommend trying all available options.

Empirically, SciRE-Solver with the multistep method can generate higher-quality samples in just a few steps and outperforms DPM-Solver++(multistep) in terms of sample quality even after 50 steps. While maintaining high quality, samples generated by SciRE-Solver using  singlestep(and fixed) and multistep methods also display selectable diversity.

# Test SciRE-Solver on EDM (1.76 FID on CIFAR-10)
- SciRE_v1-2 (singlestep_fixed) with cpkt ([edm-cifar10-32x32-cond-vp.pkl](https://nvlabs-fi-cdn.nvidia.com/edm/pretrained/edm-cifar10-32x32-cond-vp.pkl)) attian $2.29$ FID with $12$ NFE, $2.16$ FID with $14$ NFE, $1.94$ FID with $20$ NFE, $1.79$ FID with $50$ NFE, $1.76$ FID with $100$ NFE, when $\phi_1(m)=\phi_1(3)$.

# TODO:

# Acknowledgement

Our code is based on [ScoreSDE](https://github.com/yang-song/score_sde) and [DPM-Solver](https://github.com/LuChengTHU/dpm-solver).

# Citation

If you find our work beneficial to you, please consider citing:

```
@article{li2023scireAD,
  title={SciRE-Solver: Accelerating Diffusion Models Sampling  by Score-integrand Solver with Recursive Difference},
  author={Li, Shigui and Chen, Wei and Zeng, Delu},
  journal={arXiv preprint arXiv:2308.07896},
  year={2023}
}
```

