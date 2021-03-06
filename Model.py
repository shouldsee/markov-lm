
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim
# from model_mrf import *

class RNNWithSampling(nn.Module):
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len,
    ):
        super().__init__()
        self.device = device
        self.total_length = total_length
        self.min_len = min_len

        x = nn.Linear(embed_dim,total_length)
        self.latent      = nn.Parameter(x.weight.to(self.device))
        self.transition  = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.vocab       = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step      = min_len
        self.state_pref  = nn.Linear   ( embed_dim*2,  state_count).to(self.device)
        self.state_move  = nn.Embedding( state_count, embed_dim).to(self.device)

    def sample_tokens(self,z,n_step,n_sample):
        zs,lp   = self.sample_trajectory(z,n_step,n_sample)
        ys      = self.vocab(zs).log_softmax(-1)
        return ys,lp

    def sample_trajectory(self,z,n_step,n_sample):
        emits = torch.tensor([],requires_grad=True).to(self.device)
        z = z[:,None,:].repeat((1,n_sample,1))
        lp = 0
        # EPS = 1E-10
        zold = z*0
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113

            rd   = torch.rand(z.shape[:2]).to(self.device)
            # torch.rand(list(z.shape)+n_sample)
            # import pdb; pdb.set_trace()
            pref = self.state_pref(torch.cat([zold,z],dim=-1))
            xp   = pref.softmax(dim=-1)
            xpc  = xp.cumsum(-1)
            # import pdb; pdb.set_trace()
            val,which = (rd[:,:,None]<xpc).max(dim=-1)
            xps  = torch.gather(xp,dim=-1,index=which[:,:,None])[:,:,0]
            lp   = lp + torch.log(xps)

            mvs  = self.state_move(which)
            zold = z
            z    = z + mvs
            # import pdb; pdb.set_trace()
            emits= torch.cat([emits,mvs[:,None,:,:]],dim=1)

        return emits,lp


    def log_prob(self,zi,y):
        n_sample = 10
        z = self.latent[zi]
        ys,lp = self.sample_tokens(z,self.n_step,n_sample)

        ### Apply REINFORCE loss, that is weighting the loss by log(p)
        # import pdb; pdb.set_trace()
        yp    = torch.gather(ys,index=y[:,:,None,None].repeat((1,1,n_sample,1)),dim=-1)[:,:,:,0]
        return yp.mean(2)

    def log_prob_grad(self,zi,y):
        n_sample = 10
        z = self.latent[zi]
        ys,lp = self.sample_tokens(z,self.n_step,n_sample)

        ### Apply REINFORCE loss, that is weighting the loss by log(p)
        # import pdb; pdb.set_trace()
        yp    = torch.gather(ys,index=y[:,:,None,None].repeat((1,1,n_sample,1)),dim=-1)[:,:,:,0]
        gradloss = yp * lp[:,None,:]
        gradloss = gradloss.mean(-1)
        # gradloss = (yp * lp.softmax(-1)[:,None,:]).sum(-1)

        # import pdb; pdb.set_trace()
        # reinf =
        # import pdb; pdb.set_trace()
        return gradloss
    # log_prob = log_prob_grad

class RNNWithSigmoid(nn.Module):
    '''
    Well the value is now clipped but still tends to the same stationarity
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        self.device = device
        self.total_length = total_length
        self.min_len = min_len

        x = nn.Linear(embed_dim,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len

    # def forward(self,zi,n_step):
    #     z = self.latent[zi]
    #     ys = self._sample(z,n_step)
    #     return ys

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        zs = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            zs = torch.cat([zs,z[:,None]],dim=1)
            z = (z + self.transition(z))
            z = torch.tanh(z)
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob


class RNNWithMarkovNet(nn.Module):
    '''
    Use a markov transfer function to model RNN transfer.
    Model sentence as a transfer of attention
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        # state_count = 5
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*state_count*4,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor = nn.Linear(embed_dim,1).to(self.device)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        z = z.reshape((4,len(z),self.state_count,self.embed_dim))
        xq,xk,xv,xi = z
        # xtran = xq.transpose(2,1).matmul(xk).softmax(1)
        xtran = (xq.matmul(xk.transpose(2,1))/self.embed_dim**0.5).softmax(1)
        att = xi[:,:,:1].softmax(1).transpose(2,1)
        zs = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            emit = att.matmul(xv)

            # z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            # ext  = self.extractor(z).transpose(2,1).softmax(-1)
            # emit = ext.matmul(z)
            # import pdb; pdb.set_trace()
            # .matmul(z)
            zs = torch.cat([zs,emit],dim=1)
            att = att.matmul(xtran)
            # import pdb; pdb.set_trace()

            # mixed = self.transition(z).reshape((len(z), self.state_count,self.mixture_count, self.embed_dim,))

            # att = self.anchor(z).softmax(-1)
            # dz = (att[:,:,:,None] * mixed).sum(-2)
            # z = z+dz
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob


class RNNWithTransformer(nn.Module):
    '''
    Use multiple vectors instead of 1
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        state_count = 5
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*state_count,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.qmat = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.kmat = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.vmat = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor = nn.Linear(embed_dim,1).to(self.device)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        z = z.reshape((len(z),self.state_count,self.embed_dim,))
        zs = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) * 0.113
            xz = torch.cat([z,torch.ones((len(z),1,self.embed_dim)).to(self.device)],dim=1)
            xk = self.kmat(xz)
            xq = self.qmat(xz)
            xv = self.vmat(xz)
            att = xq.matmul(xk.transpose(2,1))[:,:,:-1].softmax(-1)
            dz  = att.matmul(xv[:,:-1])
            z = z+dz[:,:-1]

            # ext  = self.extractor(z).transpose(2,1).softmax(-1)
            # emit = ext.matmul(z)
            # import pdb; pdb.set_trace()
            # .matmul(z)
            emit = dz[:,-1:]
            zs = torch.cat([zs,emit],dim=1)
            # mixed = self.transition(z).reshape((len(z), self.state_count,self.mixture_count, self.embed_dim,))

            # att = self.anchor(z).softmax(-1)
            # dz = (att[:,:,:,None] * mixed).sum(-2)

            # z = z+dz
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob

class RNNWithVectorSelection(nn.Module):
    '''
    On top of SentenceAsRegression with the modification
    that emission is a weighted average of resorvior, selected by
    a attention head.
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        state_count = 5
        # state_count = 15
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*state_count,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim).to(self.device)
        # self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor = nn.Linear(embed_dim,state_count).to(self.device)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        lat = z.reshape((len(z),self.state_count,self.embed_dim))
        lat    = lat / (0.00001 + lat.std(-1,keepdims=True)) *0.113
        z   = lat[:,:1,]
        zs = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            # emit = z[:,:]
            cand = torch.cat([z[:], lat[:,1:]],dim=1)
            att  = self.extractor(z)
            # att  = att.softmax(-1)
            emit = att.matmul(cand)

            zs = torch.cat([zs,emit],dim=1)
            z = (z + self.transition(z))/2.
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        return yp

    log_prob_grad = log_prob

class RNNWithSoftmaxVectorSelection(nn.Module):
    '''
    On top of SentenceAsRegression with the modification
    that emission is a weighted average of resorvior, selected by
    a attention head.

    During the mixing step, we used an attention head.
    Using a summation allows mixing different prototypes.

    If instead, we banned the mixing by forcing sampling of discrete vectors,
    then maybe the model is more state-like

    The transition model tends to traverse the states. This is possible because
    the extractor encodes information about the slots, and is shared between
    sentences.
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        state_count = 5
        # state_count = 15
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*state_count,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim).to(self.device)
        # self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor = nn.Linear(embed_dim,state_count*2).to(self.device)

        x = nn.Linear(embed_dim*state_count,1).to(self.device)
        self.shared_kv     = nn.Parameter(x.weight)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        lat   = z.reshape((len(z),self.state_count,self.embed_dim))
        lat   = lat / (0.00001 + lat.std(-1,keepdims=True)) *0.113
        lat   = lat + (torch.rand(lat.shape).to(self.device)-0.5)*0.2

        sv = self.shared_kv.reshape((1,self.state_count,self.embed_dim))

        z   = lat[:,:1,]
        zs = torch.tensor([],requires_grad=True).to(self.device)
        self.atts = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            # emit = z[:,:]
            cand = torch.cat([z[:], lat[:,1:]],dim=1)
            cand = torch.cat([cand,sv.repeat((len(z),1,1))],dim=1)
            att  = self.extractor(z)
            att  = att.softmax(-1)
            # att  = att.max()
            emit = att.matmul(cand)
            self.atts = torch.cat([self.atts,att],dim=1)

            zs = torch.cat([zs,emit],dim=1)
            z = (z + self.transition(z))/2.
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        return yp

    log_prob_grad = log_prob



class RNNWithMixedProjection(nn.Module):
    '''
    Sentence is still represented as a vector.

    Different from vanilla RNN, where words are emitted from vector under a
    hidden vector equipped transfer function, which renders the model weird
    in the sense that the hidden state at the second word represents the
    sentence minus the first word.

    In this sense, the tone of the sentence is coupled with the underlying
    representation. In order to decouple the underlying fact (subject, verb)
    from the expression, we consider sentence manifestation as projections
    of a shared vector.

    The order of projection (for example, subject-verb-object), are common
    occuring patterns, and we continue use an RNN to encode this order.
    Together, we emit a sequence f(v,i) as manifestation of sentence v
    under emittor f using i as the initial projection.

    Simple solutions emerge from this setup where machine learns a trivial
    order of projection as (token_1, token_2, token_3) and rearrange the
    underlying vector in the same fashion.

    i needs to transit into i+1 to generate next token, either could depend
    on v or independent of v

    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        state_count = 2
        # state_count = 15
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*2,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.trans_k = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.trans_v = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.emit_k  = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.emit_v  = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        # self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        # self.extractor = nn.Linear(embed_dim,state_count*2).to(self.device)

        x = nn.Linear(embed_dim*state_count,1).to(self.device)
        # self.shared_kv     = nn.Parameter(x.weight)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        lat   = z.reshape((len(z),2,self.embed_dim))
        lat   = lat / (0.00001 + lat.std(-1,keepdims=True)) *0.113
        lat   = lat + (torch.rand(lat.shape).to(self.device)-0.5)*0.05
        # sv = self.shared_kv.reshape((1,self.state_count,self.embed_dim))

        z0   = lat[:,:1,]
        z    = lat[:,1:,]
        zs = torch.tensor([],requires_grad=True).to(self.device)
        self.atts = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            emit_k = self.emit_k(z0).reshape((len(z),self.mixture_count,self.embed_dim,))
            emit_v = self.emit_v(z0).reshape((len(z),self.mixture_count,self.embed_dim,))
            att = z.matmul(emit_k.transpose(2,1))
            att = att.softmax(-1)
            emit   = att.matmul(emit_v)
            self.atts = torch.cat([self.atts,att],dim=1)

            trans_k = self.trans_k(z0).reshape((len(z),self.mixture_count,self.embed_dim,))
            trans_v = self.trans_v(z0).reshape((len(z),self.mixture_count,self.embed_dim,))
            att = z.matmul(trans_k.transpose(2,1))
            att = att.softmax(-1)
            dz  = att.matmul(trans_v)
            z  = dz
            zs = torch.cat([zs,emit],dim=1)
            # emit = z[:,:]
            # cand = torch.cat([z[:], lat[:,1:]],dim=1)
            # cand = torch.cat([cand,sv.repeat((len(z),1,1))],dim=1)
            # att  = self.extractor(z)
            # att  = att.softmax(-1)
            # att  = att.max()
            # emit = att.matmul(cand)
            # self.atts = torch.cat([self.atts,att],dim=1)
            #
            # z = (z + self.transition(z))/2.
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        return yp

    log_prob_grad = log_prob

class RNNWithMixedProjectionSimpleTransition(nn.Module):
    '''
    Sentence is still represented as a vector.

    Different from vanilla RNN, where words are emitted from vector under a
    hidden vector equipped transfer function, which renders the model weird
    in the sense that the hidden state at the second word represents the
    sentence minus the first word.

    In this sense, the tone of the sentence is coupled with the underlying
    representation. In order to decouple the underlying fact (subject, verb)
    from the expression, we consider sentence manifestation as projections
    of a shared vector.

    The order of projection (for example, subject-verb-object), are common
    occuring patterns, and we continue use an RNN to encode this order.
    Together, we emit a sequence f(v,i) as manifestation of sentence v
    under emittor f using i as the initial projection.

    Simple solutions emerge from this setup where machine learns a trivial
    order of projection as (token_1, token_2, token_3) and rearrange the
    underlying vector in the same fashion.

    i needs to transit into i+1 to generate next token, either could depend
    on v or independent of v

    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        state_count = 2
        # state_count = 15
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*2,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        # self.trans_k = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        # self.trans_v = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.transition = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.emit_k  = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.emit_v  = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        # self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        # self.extractor = nn.Linear(embed_dim,state_count*2).to(self.device)

        x = nn.Linear(embed_dim*state_count,1).to(self.device)
        # self.shared_kv     = nn.Parameter(x.weight)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        lat   = z.reshape((len(z),2,self.embed_dim))
        lat   = lat / (0.00001 + lat.std(-1,keepdims=True)) *0.113
        lat   = lat + (torch.rand(lat.shape).to(self.device)-0.5)*0.05
        # sv = self.shared_kv.reshape((1,self.state_count,self.embed_dim))

        z0   = lat[:,:1,]
        z    = lat[:,1:,]
        zs = torch.tensor([],requires_grad=True).to(self.device)
        self.atts = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            emit_k = self.emit_k(z0).reshape((len(z),self.mixture_count,self.embed_dim,))
            emit_k = emit_k / (0.00001 + emit_k.std(-1,keepdims=True)) *0.113
            emit_v = self.emit_v(z0).reshape((len(z),self.mixture_count,self.embed_dim,))
            emit_v = emit_v / (0.00001 + emit_v.std(-1,keepdims=True)) *0.113
            att    = z.matmul(emit_k.transpose(2,1))
            att    = att.softmax(-1)
            emit   = att.matmul(emit_v)
            self.atts = torch.cat([self.atts,att],dim=1)

            # trans_k = self.trans_k(z0).reshape((len(z),self.mixture_count,self.embed_dim,))
            # trans_v = self.trans_v(z0).reshape((len(z),self.mixture_count,self.embed_dim,))
            # att = z.matmul(trans_k.transpose(2,1))
            # att = att.softmax(-1)
            # dz  = att.matmul(trans_v)
            # z  = dz
            zs = torch.cat([zs,emit],dim=1)
            z = (self.transition(z))/2.
            # emit = z[:,:]
            # cand = torch.cat([z[:], lat[:,1:]],dim=1)
            # cand = torch.cat([cand,sv.repeat((len(z),1,1))],dim=1)
            # att  = self.extractor(z)
            # att  = att.softmax(-1)
            # att  = att.max()
            # emit = att.matmul(cand)
            # self.atts = torch.cat([self.atts,att],dim=1)
            #
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        return yp

    log_prob_grad = log_prob


class RNNWithAttentionVectorSelection(nn.Module):
    '''
    On top of SentenceAsRegression with the modification
    that emission is a weighted average of resorvior, selected by
    a attention head.

    During the mixing step, we used an attention head.
    Using a summation allows mixing different prototypes.

    If instead, we banned the mixing by forcing sampling of discrete vectors,
    then maybe the model is more state-like

    The transition model tends to traverse the states. This is possible because
    the extractor encodes information about the slots, and is shared between
    sentences.
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        state_count = 5
        # state_count = 15
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*state_count,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim).to(self.device)
        # self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor = nn.Linear(embed_dim,state_count*2).to(self.device)

        x = nn.Linear(embed_dim*state_count,1).to(self.device)
        self.shared_kv     = nn.Parameter(x.weight)
        self.attk       = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.attv       = nn.Linear(embed_dim,embed_dim).to(self.device)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        lat   = z.reshape((len(z),self.state_count,self.embed_dim))
        lat   = lat / (0.00001 + lat.std(-1,keepdims=True)) *0.113
        lat   = lat + (torch.rand(lat.shape).to(self.device)-0.5)*0.2

        sv = self.shared_kv.reshape((1,self.state_count,self.embed_dim))

        z   = lat[:,:1,]
        zs = torch.tensor([],requires_grad=True).to(self.device)
        self.atts = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            # emit = z[:,:]
            cand = torch.cat([z[:], lat[:,1:]],dim=1)
            cand = torch.cat([cand, sv.repeat((len(z),1,1))],dim=1)
            # att  = self.extractor(z)

            att = z.matmul(self.attk(cand).transpose(2,1))
            att  = att.softmax(-1)
            emit = att.matmul(self.attv(cand))
            self.atts = torch.cat([self.atts,att],dim=1)

            zs = torch.cat([zs,emit],dim=1)
            z = (z + self.transition(z))/2.
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        return yp

    log_prob_grad = log_prob



class RNNWithHardmaxVectorSelection(nn.Module):
    '''
    On top of SentenceAsRegression with the modification
    that emission is a weighted average of resorvior, selected by
    a attention head.
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        state_count = 5
        # state_count = 15
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*state_count,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim).to(self.device)
        # self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor = nn.Linear(embed_dim,state_count).to(self.device)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        lat = z.reshape((len(z),self.state_count,self.embed_dim))
        lat    = lat / (0.00001 + lat.std(-1,keepdims=True)) *0.113
        lat   = lat + (torch.rand(lat.shape).to(self.device)-0.5)*0.2
        z   = lat[:,:1,]
        zs = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            # emit = z[:,:]
            cand = torch.cat([z[:], lat[:,1:]],dim=1)
            att  = self.extractor(z)
            index = att.argmax(-1)
            # att  = att.softmax(-1)
            # att  = att.max()
            # emit = att.matmul(cand)
            # import pdb; pdb.set_trace()
            emit = torch.gather(cand,dim=1,index=index[:,:,None].repeat((1,1,self.embed_dim)))

            zs = torch.cat([zs,emit],dim=1)
            z = (z + self.transition(z))/2.
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        return yp

    log_prob_grad = log_prob


class RNNWithParellelVector(nn.Module):
    '''
    Use multiple vectors instead of 1
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        # state_count = 5
        state_count = 15
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*state_count,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor = nn.Linear(embed_dim,1).to(self.device)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        z = z.reshape((len(z),self.state_count,self.embed_dim,))
        z = z
        return z

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob

# class RNNWithMixtureMultipleVector(nn.Module):
class RNNWithMatrixParameter(nn.Module):
    '''
    Use multiple vectors instead of 1
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        # state_count = 5
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        # x = nn.Linear(2*embed_dim*state_count,total_length).to(self.device)
        x = nn.Linear(embed_dim*embed_dim,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.anchor     = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor  = nn.Linear(embed_dim,1).to(self.device)

        self.shared_state = 10

        x = nn.Linear(2*embed_dim,self.shared_state).to(self.device)
        # self.shared_kv = nn.Parameter(x.weight)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        trans = z.reshape((len(z),self.embed_dim,self.embed_dim))
        # xk,xv = z.reshape((len(z),2,self.state_count,self.embed_dim,)).transpose(0,1)
        # sk,sv = self.shared_kv.reshape((1,2,self.shared_state,self.embed_dim)).transpose(0,1)
        zs = torch.tensor([],requires_grad=True).to(self.device)
        self.atts = torch.tensor([],requires_grad=True).to(self.device)
        # z = mixed[:,0:1,:]*0+1
        # z = xk[:,0:1,:]
        z = trans[:,:1,:]
        trans = trans
        # z = torch.ones()
        # xk = xk * 100
        # xv = xv * 100
        # mixed   = mixed / (0.00001 + z.std(-1,keepdims=True))  *0.113
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True))  *0.113
            # z = torch.tanh(z + self.transition(z))
            # z = (z + self.transition(z))
            #
            # att  = z.matmul(xk.transpose(2,1))
            #
            # #### [TBC[ Omittable?
            # # att = att.softmax(-1)
            #
            # self.atts = torch.cat([self.atts,att],dim=1)
            # # print(att.shape)
            # # import pdb; pdb.set_trace()
            # emit = att[:,:,:self.state_count].matmul(xv)
            emit = z.matmul(trans)
            # emit = emit+ att[:,:,self.state_count:].matmul(sv)
            dz = emit #+ self.transition(z)
            z = dz
            # z = dz

            # import pdb; pdb.set_trace()
            zs = torch.cat([zs,emit],dim=1)
            # # z = z + self.transition(z)
            # z = z+dz

        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob

# class RNNWithMixtureMultipleVector(nn.Module):
class RNNWithMovingAttentionKV(nn.Module):
    '''
    Use multiple vectors instead of 1
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        # state_count = 5
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(2*embed_dim*state_count,total_length).to(self.device)
        # x = nn.Linear(embed_dim*embed_dim,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.anchor     = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor  = nn.Linear(embed_dim,1).to(self.device)

        self.shared_state = 10

        x = nn.Linear(2*embed_dim,self.shared_state).to(self.device)
        self.shared_kv = nn.Parameter(x.weight)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        # trans = z.reshape((len(z),self.embed_dim,self.embed_dim))
        xk,xv = z.reshape((len(z),2,self.state_count,self.embed_dim,)).transpose(0,1)
        sk,sv = self.shared_kv.reshape((1,2,self.shared_state,self.embed_dim)).transpose(0,1)
        zs = torch.tensor([],requires_grad=True).to(self.device)
        self.atts = torch.tensor([],requires_grad=True).to(self.device)
        # z = mixed[:,0:1,:]*0+1
        z = xk[:,0:1,:]
        # z = trans[:,:1,:]
        # trans = trans
        # z = torch.ones()
        # xk = xk * 100
        # xv = xv * 100
        # mixed   = mixed / (0.00001 + z.std(-1,keepdims=True))  *0.113
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True))  *0.113
            # z = torch.tanh(z + self.transition(z))
            # z = (z + self.transition(z))
            #
            # att  = z.matmul(xk.transpose(2,1))
            att  = z.matmul(sk.transpose(2,1))
            #
            # #### [TBC[ Omittable?
            att = att.softmax(-1)
            #
            # self.atts = torch.cat([self.atts,att],dim=1)
            # # print(att.shape)
            # # import pdb; pdb.set_trace()
            emit = att[:,:,:self.state_count].matmul(sv)
            # emit = z.matmul(trans)
            # emit = emit+ att[:,:,self.state_count:].matmul(sv)
            dz = emit #+ self.transition(z)
            # z = z + dz
            z = dz
            # z = dz

            # import pdb; pdb.set_trace()
            zs = torch.cat([zs,emit],dim=1)
            # # z = z + self.transition(z)
            # z = z+dz

        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob


class RNNWithMovingAttention(nn.Module):
    '''
    Use multiple vectors instead of 1
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        # state_count = 5
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(2*embed_dim*state_count,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.anchor     = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor  = nn.Linear(embed_dim,1).to(self.device)

        self.shared_state = 10

        x = nn.Linear(2*embed_dim,self.shared_state).to(self.device)
        # self.shared_kv = nn.Parameter(x.weight)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        xk,xv = z.reshape((len(z),2,self.state_count,self.embed_dim,)).transpose(0,1)
        # sk,sv = self.shared_kv.reshape((1,2,self.shared_state,self.embed_dim)).transpose(0,1)
        zs = torch.tensor([],requires_grad=True).to(self.device)
        # z = mixed[:,0:1,:]*0+1
        z = xk[:,0:1,:]
        # z = torch.ones()
        xk = xk * 100
        xv = xv * 100
        # mixed   = mixed / (0.00001 + z.std(-1,keepdims=True))  *0.113
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True))  *0.113
            # z = torch.tanh(z + self.transition(z))
            # z = (z + self.transition(z))
            att  = z.matmul(xk.transpose(2,1))
            # att = torch.cat([att, z.matmul(sw.transpose(2,1))],dim=-1)
            att = att.softmax(-1)
            # import pdb; pdb.set_trace()
            emit = att[:,:,:self.state_count].matmul(xv)
            # emit = emit+ att[:,:,self.state_count:].matmul(sv)

            # import pdb; pdb.set_trace()
            zs = torch.cat([zs,emit],dim=1)
            # z = z + self.transition(z)
            mixed2 = self.transition(z).reshape((len(z), self.mixture_count, self.embed_dim,))
            att = self.anchor(z).softmax(-1)
            dz = att.matmul(mixed2) + emit
            # dz = (att[:,:,:,None] * mixed).sum(-2)
            z = z+dz

        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob


class RNNWithMixtureMultipleVector(nn.Module):
    '''
    Use multiple vectors instead of 1
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        # state_count = 5
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim
        self.state_count = state_count

        x = nn.Linear(embed_dim*state_count,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len
        self.extractor = nn.Linear(embed_dim,1).to(self.device)

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        z = z.reshape((len(z),self.state_count,self.embed_dim,))
        zs = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            ext  = self.extractor(z).transpose(2,1).softmax(-1)
            emit = ext.matmul(z)
            # import pdb; pdb.set_trace()
            # .matmul(z)
            zs = torch.cat([zs,emit],dim=1)
            mixed = self.transition(z).reshape((len(z), self.state_count,self.mixture_count, self.embed_dim,))

            att = self.anchor(z).softmax(-1)
            dz = (att[:,:,:,None] * mixed).sum(-2)

            z = z+dz
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob

class RNNWithMixture(nn.Module):
    '''
    Better expression achieved
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.mixture_count = mixture_count
        self.embed_dim = embed_dim


        x = nn.Linear(embed_dim,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim*mixture_count).to(self.device)
        self.anchor = nn.Linear(embed_dim,mixture_count).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len


    # def forward(self,zi,n_step):
    #     z = self.latent[zi]
    #     ys = self._sample(z,n_step)
    #     return ys

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        zs = torch.tensor([],requires_grad=True).to(self.device)
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            zs = torch.cat([zs,z[:,None]],dim=1)
            mixed = self.transition(z).reshape((len(z), self.mixture_count, self.embed_dim,))
            # self.anchor(z)[:,0]
            att = self.anchor(z).softmax(-1)
            dz = att[:,None,:].matmul(mixed)[:,0]
            z = z+dz
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob

class SentenceAsRegression(nn.Module):
    '''
    We consider a generative model where RNN is used to map R^N to conditional
    independent distributions corresponding to sentences. The sentence representation
    is learnt jointly with the mapping.

    The model is desgined so that the decoding is fast and straight forward,
    so that we can have a better-defined decoder than BeamSearch on a daunting
    phase space.

    We uses a generative loss function to optimise the likelihood

    The encoder is implicit and one can use
    gradient or brute-force optimisation to encode a given sentence.

    Adagrad is orders slower than RMSprop
    Using RMSprop one can easily fit an RNN model that
    transform R^N to sequences of one-hot vectors under cross_entropy loss
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count,total_length,min_len):
        super().__init__()
        self.device = device
        self.total_length = total_length
        self.min_len = min_len
        self.state_count = 1
        self.embed_dim   = embed_dim

        x = nn.Linear(embed_dim,total_length).to(self.device)
        self.latent     = nn.Parameter(x.weight)
        self.transition = nn.Linear(embed_dim,embed_dim).to(self.device)
        self.vocab      = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.n_step     = min_len

    # def forward(self,zi,n_step):
    #     z = self.latent[zi]
    #     ys = self._sample(z,n_step)
    #     return ys

    def sample_tokens(self,z,n_step):
        zs = self.sample_trajectory(z,n_step)
        ys = self.vocab(zs).log_softmax(-1)
        return ys

    def sample_trajectory(self,z,n_step):
        zs = torch.tensor([],requires_grad=True).to(self.device)
        # z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
        for i in range(n_step):
            z    = z / (0.00001 + z.std(-1,keepdims=True)) *0.113
            z   = z + (torch.rand(z.shape).to(self.device)-0.5)*0.05
            z  =  z
            zs = torch.cat([zs,z[:,None]],dim=1)
            z = (z + self.transition(z))/2.
        self.atts = zs[:,:,:1]*0+1
        return zs

    def log_prob(self,zi,y):
        z = self.latent[zi]
        ys = self.sample_tokens(z,self.n_step)
        yp = torch.gather(ys,index=y[:,:,None],dim=-1)[:,:,0]
        # import pdb; pdb.set_trace()
        return yp
    log_prob_grad = log_prob


class ExtractionAndCNNTemplateMatching(nn.Module):
    '''
    A shallow network extract tokens from a sentence.
    And tries to recover the sentences by performing
    a guided random walk from the tokens.

    Hard part: encoding the guide matrix. The token
    can be crudly extracted with. At least two ways to do this.

    Using a sequence template matching for now where each state
    is a weighted average of the extracted states.

    The temperature of initialisation is important, but
    the model is unable to correctly partition the sequences
    correctly. I guess that model is too dependent on the initialisation
    and without a correct initialisation of the templates there is no chance
    of correct grouping the sentences into meaningful categories. In other
    words, sequence space is N^L and requires better tricks to cover.

    The progess of transition matrix is snapshoted in vis1.py

    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count):
        super().__init__()
        self.embed_dim     = embed_dim
        self.graph_dim     = graph_dim
        self.mixture_count = mixture_count
        self.state_count   = state_count
        self.output_count  = output_count = 30
        # graph_dim = english_vocab_len
        self.device = device
        self.embed = nn.Embedding(graph_dim,embed_dim).to(self.device)
        self.vocab = nn.Linear(embed_dim,graph_dim,).to(self.device)

        x = nn.Linear(state_count,state_count*mixture_count).to(self.device)
        self.init_dist = nn.Parameter(x.bias.reshape((1,mixture_count,state_count)))
        self.transition= nn.Parameter(x.weight.reshape((1,mixture_count,state_count,state_count)))
        x = nn.Linear(embed_dim  *state_count,mixture_count).to(self.device)
        self.state_vect= nn.Parameter(x.weight.reshape((1,mixture_count,state_count,embed_dim)))
        x = nn.Linear(state_count*output_count,mixture_count).to(self.device)
        self.template  = nn.Parameter(x.weight.reshape(1,mixture_count,output_count,state_count,))
        # self._template = torch.softmax(self.template,3)



    # def log_prob(self,x):
    def log_prob(self,zi,x):
        z = self.log_prob_chain(x)
        z = torch.logsumexp(z,dim=(1,))
        # z = z.max(1)[0]
        return z
    log_prob_grad = log_prob
    @property
    def _template(self):
        return 1* self.template

    def log_prob_chain(self,x,dbg=0):
        x0 = x
        xx = self.embed(x)
        zs = torch.tensor([],requires_grad=True)
        z = torch.log_softmax(self.init_dist[:,:,:,None],dim=2)
        # self._template = 10* self.template
        ### calculate the value of extracted nodes
        att = torch.softmax(xx[:,:,:self.state_count],dim=1)
        states = att.transpose(1,2).matmul(xx)[:,None,:,:]
        # emit = torch.log_softmax(self.vocab(0*self.state_vect+xx.mean(1)[:,None,None]),dim=-1)
        emit = torch.log_softmax(self.vocab(states),dim=-1)
        rev = torch.softmax(self._template,3).matmul(states)
        emm = self.vocab(rev).log_softmax(-1)
        z = torch.gather(emm,dim=-1,index=x[:,None,:,None].repeat(1,self.mixture_count,1,1))
        z = z[:,:,:,0].mean(-1)
        if dbg:
            import pdb; pdb.set_trace()
        return z


    def log_prob_cluster(self,x,dbg=0):
        z = self.log_prob_chain(x,dbg).log_softmax(-1)
        return z

class ExtractionAndMarkovTemplateMatching(nn.Module):
    '''
    A shallow network extract tokens from a sentence.
    And tries to recover the sentences by performing
    a guided random walk from the tokens.

    Hard part: encoding the guide matrix. The token
    can be crudly extracted with. At least two ways to do this.

    Assuming tokens extracted with the eye matrix. The transition
    matrix could be either obtained in a mixture manner by computing
    K markov models simultaneously, or in an encoder manner by directly
    extraction from the sentence.
    '''
    def __init__(self, device, graph_dim,embed_dim,mixture_count,state_count):
        super().__init__()
        self.embed_dim     = embed_dim
        self.graph_dim     = graph_dim
        self.mixture_count = mixture_count
        self.state_count   = state_count
        # graph_dim = english_vocab_len
        self.device = device
        self.embed = nn.Embedding(graph_dim,embed_dim).to(self.device)
        self.vocab = nn.Linear(embed_dim,graph_dim,).to(self.device)

        x = nn.Linear(state_count,state_count*mixture_count).to(self.device)
        self.init_dist = nn.Parameter(x.bias.reshape((1,mixture_count,state_count)))
        self.transition= nn.Parameter(x.weight.reshape((1,mixture_count,state_count,state_count)))
        x = nn.Linear(embed_dim*state_count,mixture_count).to(self.device)
        self.state_vect= nn.Parameter(x.weight.reshape((1,mixture_count,state_count,embed_dim)))



    def log_prob(self,zi,x):
        z = self.log_prob_chain(x)
        z = torch.logsumexp(z,dim=(2,1,))
        return z
        p = z.softmax(1)
        return (p*z).sum(1)
        # return z

    def log_prob_chain(self,x):
        x0 = x
        xx = self.embed(x)
        zs = torch.tensor([],requires_grad=True)
        z = torch.log_softmax(self.init_dist[:,:,:,None],dim=2)
        logtransition = torch.log_softmax(self.transition+5*torch.eye(self.state_count)[None,None,:,:,],3)

        ### calculate the value of extracted nodes
        att = torch.softmax(xx[:,:,:self.state_count],dim=1)
        states = att.transpose(1,2).matmul(xx)
        # emit = torch.log_softmax(self.vocab(0*self.state_vect+xx.mean(1)[:,None,None]),dim=-1)
        emit = torch.log_softmax(self.vocab(states[:,None,:,:]),dim=-1)
        # print(emit.shape)
        # import pdb; pdb.set_trace()
        for i in range(x.shape[1]):
            # xxx = ( xx[:,i:i+1,:])
            z = torch.logsumexp(logtransition + z,dim=2)[:,:,:,None]
            x = torch.cat([x0[:,None,i:i+1,None] for _ in range(self.mixture_count)],dim=1)
            x = torch.cat([x for _ in range(self.state_count)],dim=2)
            x = torch.gather(emit+(0*x),index=x,dim=-1)
            # import pdb; pdb.set_trace()
            z = x + z
        z = z/1./(i+1)
        return z

    def log_prob_cluster(self,x):
        z = self.log_prob_chain(x)
        z = z * x.shape[1]
        logp = z.logsumexp(dim=2).log_softmax(dim=1)[:,:,0]
        return logp

class MixtureOfHMM(nn.Module):
    def __init__(self, graph_dim,embed_dim,mixture_count,state_count,device):
        super().__init__()
        # graph_dim = english_vocab_len
        self.device = device
        self.embed = nn.Embedding(graph_dim,embed_dim).to(self.device)
        # self.vocab = nn.Linear(graph_dim,embed_dim).to(self.device)
        self.vocab = nn.Linear(embed_dim,graph_dim,).to(self.device)
        self.mixture_count = mixture_count
        self.state_count = state_count

        x = nn.Linear(state_count,state_count*mixture_count).to(self.device)
        self.init_dist = nn.Parameter(x.bias.reshape((1,mixture_count,state_count)))
        self.transition= nn.Parameter(x.weight.reshape((1,mixture_count,state_count,state_count)))
        x = nn.Linear(embed_dim*state_count,mixture_count).to(self.device)
        self.state_vect= nn.Parameter(x.weight.reshape((1,mixture_count,state_count,embed_dim)))
        # self.emission  = nn.Linear(state_count**2,mixture_count).to(self.device)
        # print(self.transition.shape)
        # self.init_dist

    def log_prob(self,zi,x):
        x0 = x
        xx = self.embed(x)
        zs = torch.tensor([],requires_grad=True)
        z = torch.log_softmax(self.init_dist[:,:,:,None]*100,dim=2)
        logtransition = torch.log_softmax(self.transition*100.,2)
        emit = torch.log_softmax(self.vocab(0*self.state_vect+xx.mean(1)[:,None,None]),dim=-1)
        # emit = torch.log_softmax(self.vocab(self.state_vect)/2.,dim=-1)
        # state_vect = xx
        # import pdb; pdb.set_trace()
        for i in range(x.shape[1]):
            # xxx = ( xx[:,i:i+1,:])
            z = torch.logsumexp(logtransition + z,dim=2)[:,:,:,None]
            x = torch.cat([x0[:,None,i:i+1,None] for _ in range(self.mixture_count)],dim=1)
            x = torch.cat([x for _ in range(self.state_count)],dim=2)
            x = torch.gather(emit+(0*x),index=x,dim=-1)
            z = x + z
        z = z/1./(i+1)
        z = torch.logsumexp(z,dim=(1,2))
        return z

    def decode(self,z):
        y = z
        return y

    def sample(self,size):
        torch.random()

def cross_entropy(targ,pred):
    return

    """
    Load From Checkpoint
    """
