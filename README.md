# Vigia de Preço

Painel local para consultar e acompanhar ofertas do Mercado Livre. Requer Python 3.10 ou mais recente e usa só a biblioteca padrão.

## Iniciar

1. No Windows, abra `Iniciar-Vigia.bat`. No Linux ou macOS, rode `python3 app.py`.
2. Abra [http://127.0.0.1:8765](http://127.0.0.1:8765).
3. Digite um produto ou cole o link HTTPS do anúncio e clique em **Buscar preços**.
4. Use **Colocar em vigia** para acompanhar o produto. O intervalo inicial é de 60 minutos, ajustável em **Configurações**.

O painel consulta diretamente a página pública do Mercado Livre, sem chave nem login. Se a página recusar a consulta automatizada ou mudar de formato, o painel mostra o motivo e o link para abrir a busca na loja. Como alternativa para uma busca pontual, abra a página na loja, salve com Ctrl+S como **Página da Web, somente HTML** e importe o arquivo no painel, diretamente na vigia ou nos resultados da busca. A importação lê os preços no seu computador. O servidor continua executando as vigias com o navegador fechado, enquanto o computador estiver ligado e conectado à internet.

## Preços, parcelas e frete

- O preço da página e o preço anterior aparecem quando anunciados. O total parcelado usa o total exibido na loja ou a quantidade de parcelas multiplicada pelo valor unitário.
- O frete anunciado soma ao subtotal como **estimativa**. Sem valor reconhecível, aparece o subtotal mais frete a confirmar.
- Cupons visíveis nos anúncios das vigias geram avisos em **Alertas**. O preço anunciado com cupom é condicional e o painel evita aplicar outro desconto sobre ele. Campanhas que não aparecem nos anúncios consultados ficam fora dessa detecção.
- Você pode cadastrar manualmente um cupom para simular suas regras. Para calcular o total confirmado, registre os valores e cupons aceitos no checkout para o CEP informado. Benefícios como Meli+ dependem da conta e da entrega.

As vigias e o histórico ficam na pasta `dados`. Pare o programa e copie essa pasta para fazer backup. Para mudar a porta, rode `python3 app.py --port 8766` ou, no Windows, `py -3 app.py --port 8766`.
