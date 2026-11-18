# Vigia de Preço

Painel local para consultar e acompanhar ofertas do Mercado Livre. Requer Python 3.10 ou mais recente e usa só a biblioteca padrão.

## Iniciar

1. No Windows, abra `Iniciar-Vigia.bat`. No Linux ou macOS, rode `python3 app.py`.
2. Abra [http://127.0.0.1:8765](http://127.0.0.1:8765).
3. Digite um produto ou cole o link HTTPS do anúncio e clique em **Buscar preços**.
4. Use **Colocar em vigia** para acompanhar o produto. O intervalo inicial é de 60 minutos, ajustável em **Configurações**.

Você pode consultar o Mercado Livre via Google Shopping com SerpApi, pela extensão local ou pela página pública. Se a página recusar a consulta automatizada ou mudar de formato, o painel mostra o motivo e o link para abrir a busca na loja. Como alternativa para uma busca pontual, abra a página na loja, salve com Ctrl+S como **Página da Web, somente HTML** e importe o arquivo no painel, diretamente na vigia ou nos resultados da busca. A importação lê os preços no seu computador. O servidor continua executando as vigias com o navegador fechado, enquanto o computador estiver ligado e conectado à internet.

## Busca pelo Google Shopping, sem abrir o navegador

1. Obtenha uma chave no [painel da SerpApi](https://serpapi.com/dashboard).
2. No Vigia, abra **Configurações → Busca sem navegador**, cole a chave e salve.
3. Pesquise pelo nome do produto ou deixe as vigias ativas. Mantenha o servidor rodando e conectado à internet.

Ao colar o link de um anúncio, o Vigia consulta primeiro a página pública. Se a leitura falhar, procura no índice pelo mesmo anúncio. Na busca por nome, o Vigia consulta o índice primeiro e tenta a busca pública da loja se faltar um resultado. O painel informa quando a loja recusa o acesso e o índice não tem preço.

A SerpApi recebe os termos e retorna ofertas indexadas. Quando faltam resultados, o Vigia varia os termos entre o Shopping e a busca do Google, com até quatro consultas. Para links, também pesquisa o código do anúncio. Para mangás e livros, tenta grafias como “volume 8” e “08”. Na página pública da loja pode ampliar a busca pelo nome da série e filtrar pelo volume original. O Vigia exibe só ofertas do Mercado Livre e só usa preços do Google quando o resultado do anúncio fornece um valor explícito em reais. Cobertura, frequência de atualização e quantidade de consultas dependem do índice e do plano. Frete por CEP, Meli+ e cupons de checkout ficam sujeitos a conferência na loja. A chave fica em `config.local.json` no seu computador. Para voltar à consulta anterior, remova a chave nas configurações.

## Busca automática pelo Chrome ou Edge

1. Abra o Vigia em [http://127.0.0.1:8765](http://127.0.0.1:8765).
2. No Chrome, abra `chrome://extensions`. No Edge, abra `edge://extensions`.
3. Ative o **Modo do desenvolvedor**, clique em **Carregar sem compactação** e selecione a pasta `extensao` deste projeto. Faça isso uma vez.
4. Em **Configurações** do Vigia, confira se aparece **Navegador conectado**. Use **Buscar preços** ou deixe as vigias rodarem no intervalo configurado.

A extensão abre uma aba em segundo plano, lê os dados dos anúncios do Mercado Livre e fecha a aba. Se os preços demorarem a renderizar, ela traz uma janela temporária para primeiro plano por alguns segundos e a fecha ao terminar. Depois de atualizar o projeto, recarregue a extensão para aplicar as mudanças. A extensão aguarda o servidor local confirmar a leitura dos anúncios antes de fechar a página. A tentativa expira se os preços não aparecerem. Mantenha o servidor e o navegador abertos, com a extensão ativada. A extensão usa o endereço e a porta padrão. Quando o Mercado Livre apresentar uma verificação de acesso, a extensão encerra a tentativa e pausa as consultas por 15 minutos.

## Preços, parcelas e frete

Em cada vigia você pode escolher se o preço-alvo considera o produto ou o total com frete, e se vale para pagamento à vista ou parcelado. As ofertas que passam da meta ou que ficam sem preço comparável aparecem recolhidas. Se você ativar o aviso estimado, frete e cupons pendentes são sinalizados como estimativas.

- O preço da página e o preço anterior aparecem quando anunciados. O total parcelado usa o total exibido na loja ou a quantidade de parcelas multiplicada pelo valor unitário.
- O frete anunciado soma ao subtotal como **estimativa**. Sem valor reconhecível, aparece o subtotal mais frete a confirmar.
- Cupons visíveis nos anúncios das vigias geram avisos em **Alertas**. O preço anunciado com cupom é condicional e o painel evita aplicar outro desconto sobre ele. Campanhas que não aparecem nos anúncios consultados ficam fora dessa detecção.
- Você pode cadastrar manualmente um cupom para simular suas regras. Para calcular o total confirmado, registre os valores e cupons aceitos no checkout para o CEP informado. Benefícios como Meli+ dependem da conta e da entrega.

As vigias e o histórico ficam na pasta `dados`. Pare o programa e copie essa pasta para fazer backup. Para mudar a porta, rode `python3 app.py --port 8766` ou, no Windows, `py -3 app.py --port 8766`.
